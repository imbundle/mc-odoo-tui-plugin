"""Bounded, cursor-based polling for an approved Odoo log file."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Pattern

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
TAIL_SCAN_BYTES = 1024 * 1024
_CURSOR_VERSION = 1
_CURSOR_KEY = os.environ.get("MC_ODOO_TUI_CURSOR_SECRET", "mc-odoo-tui-cursor-v1").encode()


class PollingError(Exception):
    """A safe, client-facing validation or source error."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class _Cursor:
    device: int
    inode: int
    offset: int


def _encode_cursor(cursor: _Cursor) -> str:
    payload = {"v": _CURSOR_VERSION, "d": cursor.device, "i": cursor.inode, "o": cursor.offset}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(_CURSOR_KEY, raw, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(raw + signature).decode().rstrip("=")


def _decode_cursor(value: str) -> _Cursor:
    try:
        if not value or len(value) > 512 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload, signature = raw[:-16], raw[-16:]
        if not hmac.compare_digest(signature, hmac.new(_CURSOR_KEY, payload, hashlib.sha256).digest()[:16]):
            raise ValueError
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError
        if data.get("v") != _CURSOR_VERSION:
            raise ValueError
        device, inode, offset = data["d"], data["i"], data["o"]
        if not all(type(item) is int and item >= 0 for item in (device, inode, offset)):
            raise ValueError
        return _Cursor(device, inode, offset)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeError, binascii.Error, OverflowError):
        raise PollingError(400, "INVALID_CURSOR", "The cursor is invalid or expired.") from None


@dataclass(frozen=True)
class _ParsedLine:
    timestamp: str | None
    pid: int | None
    level: str | None
    database: str | None
    logger: str | None
    message: str
    structured: bool = False


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_JSON_LINE = re.compile(r"^\s*(\{.*\})\s*$")
_ODOO_LINE = re.compile(
    r"^\s*(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+"
    r"(?P<pid>\d+)\s+(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
    r"(?P<database>\S+)\s+(?P<logger>[^:]+):\s?(?P<message>.*)$"
)
_PREFIX_LINE = re.compile(
    r"^\s*(?P<timestamp>\d{4}-\d{2}-\d{2}[T ][^ ]+)\s+(?P<level>DEBUG|ERROR|WARNING|INFO|CRITICAL)\s+(?P<message>.*)$"
)
_BRACKET_LINE = re.compile(r"^\s*\[(?P<level>DEBUG|ERROR|WARNING|INFO|CRITICAL)\]\s*(?P<message>.*)$")
_LEVEL_LINE = re.compile(r"^\s*(?P<level>DEBUG|ERROR|WARNING|INFO|CRITICAL)\s+(?P<message>.*)$")


def parse_log_line(line: bytes) -> _ParsedLine:
    """Parse common structured/plain Odoo log forms without changing the source text."""
    text = _ANSI_RE.sub("", line.rstrip(b"\r\n").decode("utf-8", errors="replace"))
    match = _ODOO_LINE.match(text)
    if match:
        values = match.groupdict()
        return _ParsedLine(
            values["timestamp"],
            int(values["pid"]),
            values["level"],
            values["database"],
            values["logger"].strip(),
            values["message"],
            True,
        )
    match = _JSON_LINE.match(text)
    if match:
        try:
            value = json.loads(match.group(1))
            if isinstance(value, dict) and "message" in value:
                level = value.get("level")
                pid = value.get("pid") if isinstance(value.get("pid"), int) else None
                database = value.get("database") if isinstance(value.get("database"), str) else None
                logger = value.get("logger") if isinstance(value.get("logger"), str) else None
                return _ParsedLine(value.get("timestamp"), pid, level, database, logger, str(value["message"]), True)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    for pattern in (_PREFIX_LINE, _BRACKET_LINE, _LEVEL_LINE):
        match = pattern.match(text)
        if match:
            return _ParsedLine(match.groupdict().get("timestamp"), None, match["level"], None, None, match["message"])
    return _ParsedLine(None, None, None, None, None, text)


class LogPoller:
    """Read a stable, bounded snapshot of one approved log path."""

    def __init__(self, path: str | os.PathLike[str], parser: Callable[[bytes], _ParsedLine] = parse_log_line):
        self.path = Path(path)
        self.parser = parser

    def poll(
        self,
        *,
        cursor: str | None = None,
        limit: int | str | None = None,
        level: str | None = None,
        pattern: str | None = None,
        tail: bool = False,
    ) -> dict:
        bounded_limit = self._validate_limit(limit)
        if level is not None and level not in {"DEBUG", "ERROR", "WARNING", "INFO", "CRITICAL"}:
            raise PollingError(400, "INVALID_LEVEL", "level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL.")
        matcher = self._compile_pattern(pattern)
        reset = False
        start = 0
        decoded = _decode_cursor(cursor) if cursor is not None else None

        try:
            source = self.path.open("rb")
        except OSError:
            raise PollingError(404, "LOG_UNAVAILABLE", "The selected log is unavailable.") from None

        try:
            opened_stat = os.fstat(source.fileno())
            stable_end = opened_stat.st_size
            if decoded is not None:
                if (decoded.device, decoded.inode) != (opened_stat.st_dev, opened_stat.st_ino) or decoded.offset > stable_end:
                    reset = True
                else:
                    start = decoded.offset
            elif tail:
                start = self._tail_offset(source, stable_end, bounded_limit)
        except OSError:
            source.close()
            raise PollingError(404, "LOG_UNAVAILABLE", "The selected log is unavailable.") from None

        identity = _Cursor(opened_stat.st_dev, opened_stat.st_ino, start)
        entries: list[dict] = []
        last_structured: dict | None = None
        offset = start
        try:
            source.seek(start)
            while offset < stable_end and len(entries) < bounded_limit:
                line = source.readline()
                if not line or not line.endswith(b"\n"):
                    break
                offset += len(line)
                if offset > stable_end:
                    break
                parsed = self.parser(line)
                if not parsed.structured and last_structured is not None:
                    if (level is None or last_structured["level"] == level) and (matcher is None or matcher.search(parsed.message) is not None):
                        if parsed.message:
                            last_structured["message"] += "\n" + parsed.message
                    continue
                if level is not None and parsed.level != level:
                    last_structured = None
                    continue
                if matcher is not None and matcher.search(parsed.message) is None:
                    last_structured = None
                    continue
                entry = {
                    "timestamp": parsed.timestamp,
                    "pid": parsed.pid,
                    "level": parsed.level,
                    "database": parsed.database,
                    "logger": parsed.logger,
                    "message": parsed.message,
                }
                entries.append(entry)
                last_structured = entry if parsed.structured else None
            has_more = len(entries) >= bounded_limit and self._has_matching_line(
                source, offset, stable_end, level, matcher
            )
        except OSError:
            source.close()
            raise PollingError(404, "LOG_UNAVAILABLE", "The selected log is unavailable.") from None
        finally:
            source.close()

        next_cursor = _encode_cursor(_Cursor(identity.device, identity.inode, offset))
        return {"entries": entries, "next_cursor": next_cursor, "has_more": has_more, "cursor_reset": reset}

    @staticmethod
    def _tail_offset(source, stable_end: int, limit: int) -> int:
        """Return an event-header offset near EOF using bounded backward context."""
        if stable_end <= 0:
            return 0
        scan_start = max(0, stable_end - TAIL_SCAN_BYTES)
        source.seek(scan_start)
        aligned = scan_start
        if scan_start:
            source.seek(scan_start - 1)
            previous = source.read(1)
            source.seek(scan_start)
            if previous != b"\n":
                source.readline()
            aligned = source.tell()
        source.seek(aligned)
        data = source.read(stable_end - aligned)
        starts = [aligned]
        structured_starts: list[int] = []
        position = aligned
        for raw_line in data.splitlines(keepends=True):
            if not raw_line.endswith(b"\n"):
                break
            if parse_log_line(raw_line).structured:
                structured_starts.append(position)
            position += len(raw_line)
            if position < stable_end:
                starts.append(position)
        if structured_starts:
            return structured_starts[-limit] if len(structured_starts) > limit else structured_starts[0]
        return starts[-limit] if len(starts) > limit else aligned

    @staticmethod
    def _validate_limit(value: int | str | None) -> int:
        if value is None:
            return DEFAULT_LIMIT
        if not isinstance(value, (int, str)) or isinstance(value, bool):
            raise PollingError(400, "INVALID_LIMIT", "limit must be an integer between 1 and 500.")
        try:
            result = int(value)
        except (TypeError, ValueError):
            raise PollingError(400, "INVALID_LIMIT", "limit must be an integer between 1 and 500.") from None
        if result < 1 or result > MAX_LIMIT or (isinstance(value, str) and str(result) != value.strip()):
            raise PollingError(400, "INVALID_LIMIT", "limit must be an integer between 1 and 500.")
        return result

    @staticmethod
    def _compile_pattern(value: str | None) -> Pattern[str] | None:
        if value is None:
            return None
        try:
            return re.compile(value)
        except re.error:
            raise PollingError(400, "INVALID_PATTERN", "pattern is not a valid regular expression.") from None

    def _has_matching_line(
        self, source, offset: int, stable_end: int, level: str | None, matcher: Pattern[str] | None
    ) -> bool:
        if offset >= stable_end:
            return False
        source.seek(offset)
        while offset < stable_end:
            line = source.readline()
            if not line or not line.endswith(b"\n") or offset + len(line) > stable_end:
                return False
            offset += len(line)
            parsed = self.parser(line)
            if (level is None or parsed.level == level) and (
                matcher is None or matcher.search(parsed.message) is not None
            ):
                return True
        return False
