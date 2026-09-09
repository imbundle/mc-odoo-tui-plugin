from pathlib import Path

import pytest

from log_polling import LogPoller, PollingError


def test_odoo_lines_expose_metadata_and_group_continuations(tmp_path: Path):
    path = tmp_path / "odoo.log"
    path.write_text(
        "2026-09-09 11:03:41,844 1090044 INFO 19_parkair werkzeug: "
        "\u001b[35mGET /websocket?version=19.0-2 HTTP/1.1\u001b[0m 500 -\n"
        "Traceback (most recent call last):\n"
        "KeyError: 'socket'\n",
        encoding="utf-8",
    )

    result = LogPoller(path).poll(limit=10)

    assert result["entries"] == [{
        "timestamp": "2026-09-09 11:03:41,844",
        "pid": 1090044,
        "level": "INFO",
        "database": "19_parkair",
        "logger": "werkzeug",
        "message": "GET /websocket?version=19.0-2 HTTP/1.1 500 -\nTraceback (most recent call last):\nKeyError: 'socket'",
    }]


def test_reads_bounded_batches_and_advances_cursor(tmp_path: Path):
    path = tmp_path / "odoo.log"
    path.write_text("INFO first\nERROR second\nINFO third\n", encoding="utf-8")
    poller = LogPoller(path)

    first = poller.poll(limit=1)
    assert [entry["message"] for entry in first["entries"]] == ["first"]
    assert first["has_more"] is True
    second = poller.poll(cursor=first["next_cursor"], limit=1)
    assert [entry["message"] for entry in second["entries"]] == ["second"]


def test_cursor_boundary_is_exact_and_does_not_duplicate_or_skip_lines(tmp_path: Path):
    path = tmp_path / "odoo.log"
    path.write_text("INFO first\nINFO second\nINFO third\n", encoding="utf-8")
    poller = LogPoller(path)

    first = poller.poll(limit=2)
    assert [entry["message"] for entry in first["entries"]] == ["first", "second"]
    assert first["has_more"] is True

    final = poller.poll(cursor=first["next_cursor"], limit=2)
    assert [entry["message"] for entry in final["entries"]] == ["third"]
    assert final["has_more"] is False

    at_eof = poller.poll(cursor=final["next_cursor"], limit=2)
    assert at_eof["entries"] == []
    assert at_eof["has_more"] is False
    assert at_eof["next_cursor"] == final["next_cursor"]


def test_filtered_scan_advances_past_nonmatching_lines(tmp_path: Path):
    path = tmp_path / "odoo.log"
    path.write_text("INFO first\nERROR second\nINFO third\n", encoding="utf-8")
    poller = LogPoller(path)

    result = poller.poll(level="ERROR", limit=1)
    assert [entry["message"] for entry in result["entries"]] == ["second"]
    assert result["has_more"] is False
    assert poller.poll(cursor=result["next_cursor"], level="ERROR")["entries"] == []


def test_regex_filter_and_empty_matches_are_bounded_and_progressing(tmp_path: Path):
    path = tmp_path / "odoo.log"
    path.write_text("INFO alpha\nINFO beta\nINFO alphabet\n", encoding="utf-8")
    poller = LogPoller(path)

    matching = poller.poll(pattern=r"^alpha", limit=1)
    assert [entry["message"] for entry in matching["entries"]] == ["alpha"]
    assert matching["has_more"] is True
    next_matching = poller.poll(cursor=matching["next_cursor"], pattern=r"^alpha", limit=1)
    assert [entry["message"] for entry in next_matching["entries"]] == ["alphabet"]
    assert next_matching["has_more"] is False

    empty_pattern = poller.poll(pattern="", limit=2)
    assert [entry["message"] for entry in empty_pattern["entries"]] == ["alpha", "beta"]
    assert empty_pattern["has_more"] is True


@pytest.mark.parametrize("limit", [0, -1, 501, "0", "501", "1.0", "abc", "+1"])
def test_invalid_limits_are_rejected_without_clamping(tmp_path: Path, limit):
    poller = LogPoller(tmp_path / "odoo.log")
    with pytest.raises(PollingError) as limit_error:
        poller.poll(limit=limit)
    assert limit_error.value.code == "INVALID_LIMIT"


def test_limit_bounds_and_result_count_are_enforced(tmp_path: Path):
    path = tmp_path / "odoo.log"
    path.write_text("".join(f"INFO line-{number}\n" for number in range(600)), encoding="utf-8")
    poller = LogPoller(path)

    result = poller.poll(limit=500)
    assert len(result["entries"]) == 500
    assert result["has_more"] is True
    remainder = poller.poll(cursor=result["next_cursor"], limit=500)
    assert len(remainder["entries"]) == 100
    assert remainder["has_more"] is False


def test_invalid_pattern_and_level_are_structured_errors(tmp_path: Path):
    poller = LogPoller(tmp_path / "odoo.log")
    with pytest.raises(PollingError) as pattern_error:
        poller.poll(pattern="[")
    assert pattern_error.value.code == "INVALID_PATTERN"
    with pytest.raises(PollingError) as level_error:
        poller.poll(level="error")
    assert level_error.value.code == "INVALID_LEVEL"


def test_malformed_cursor_is_rejected_even_when_file_is_missing(tmp_path: Path):
    with pytest.raises(PollingError) as error:
        LogPoller(tmp_path / "missing.log").poll(cursor="not-a-cursor")
    assert error.value.code == "INVALID_CURSOR"


def test_rotation_resets_to_the_new_file(tmp_path: Path):
    path = tmp_path / "odoo.log"
    path.write_text("INFO old\n", encoding="utf-8")
    poller = LogPoller(path)
    cursor = poller.poll()["next_cursor"]

    rotated = tmp_path / "rotated.log"
    rotated.write_text("INFO new\n", encoding="utf-8")
    rotated.replace(path)

    result = poller.poll(cursor=cursor)
    assert [entry["message"] for entry in result["entries"]] == ["new"]
    assert result["cursor_reset"] is True


def test_truncation_resets_same_file(tmp_path: Path):
    path = tmp_path / "odoo.log"
    path.write_text("INFO first\nINFO second\n", encoding="utf-8")
    poller = LogPoller(path)
    cursor = poller.poll(limit=1)["next_cursor"]
    path.write_text("INFO new\n", encoding="utf-8")

    result = poller.poll(cursor=cursor)
    assert [entry["message"] for entry in result["entries"]] == ["new"]
    assert result["cursor_reset"] is True


def test_rotation_and_truncation_do_not_repeat_old_entries(tmp_path: Path):
    path = tmp_path / "odoo.log"
    path.write_text("INFO old-1\nINFO old-2\n", encoding="utf-8")
    poller = LogPoller(path)
    old_cursor = poller.poll(limit=1)["next_cursor"]

    rotated = tmp_path / "rotated.log"
    rotated.write_text("INFO rotated-1\nINFO rotated-2\n", encoding="utf-8")
    rotated.replace(path)
    rotated_result = poller.poll(cursor=old_cursor, limit=1)
    assert [entry["message"] for entry in rotated_result["entries"]] == ["rotated-1"]
    assert rotated_result["cursor_reset"] is True

    path.write_text("INFO x\n", encoding="utf-8")
    truncated_result = poller.poll(cursor=rotated_result["next_cursor"], limit=1)
    assert [entry["message"] for entry in truncated_result["entries"]] == ["x"]
    assert truncated_result["cursor_reset"] is True


def test_partial_final_line_is_deferred(tmp_path: Path):
    path = tmp_path / "odoo.log"
    path.write_bytes(b"INFO complete\nINFO partial")
    poller = LogPoller(path)
    result = poller.poll()
    assert [entry["message"] for entry in result["entries"]] == ["complete"]
    path.write_bytes(b"INFO complete\nINFO partial\n")
    resumed = poller.poll(cursor=result["next_cursor"])
    assert [entry["message"] for entry in resumed["entries"]] == ["partial"]


def test_polling_returns_bounded_snapshot_not_a_stream(tmp_path: Path):
    path = tmp_path / "odoo.log"
    path.write_text("INFO one\nINFO two\n", encoding="utf-8")

    result = LogPoller(path).poll(limit=1)

    assert isinstance(result, dict)
    assert set(result) == {"entries", "next_cursor", "has_more", "cursor_reset"}
    assert not hasattr(result, "__next__")
