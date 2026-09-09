# Bounded log polling contract

This is the plugin-owned application contract for the first log API. It is deliberately request/response based: no SSE, WebSocket, or long-lived connection is part of this contract.

## Endpoint

`GET /api/local/odoo-tui/instance/logs?client=<client>`

Query parameters:

| Parameter | Type | Required | Semantics |
| --- | --- | --- | --- |
| `cursor` | opaque string | no | Position returned by a previous response. Omission starts at the beginning of the current file. |
| `limit` | positive integer | no | Maximum number of returned entries. Default `100`; accepted range `1..500` inclusive. |
| `level` | `ERROR`, `WARNING`, or `INFO` | no | Exact severity filter. Matching is case-sensitive; an unsupported value is a validation error. |
| `pattern` | regular-expression string | no | Python regular expression matched against the parsed entry message. It is not a shell expression and is never evaluated by the frontend. |

The backend resolves the required URL-encoded `client` query parameter to a registered instance and its approved log file. The browser cannot provide a filesystem path.

## Entry and response shape

The transport-neutral entry shape is:

```json
{
  "timestamp": "2026-09-08T12:34:56.123Z",
  "level": "INFO",
  "message": "server ready"
}
```

`timestamp` is an ISO-8601 string when present in the source log. `level` is one of `ERROR`, `WARNING`, or `INFO`; entries that cannot be assigned one of these levels retain their parsed level according to the existing log model and are not returned when a level filter is supplied. `message` excludes the physical line terminator.

A successful response is:

```json
{
  "entries": [],
  "next_cursor": "<opaque-cursor>",
  "has_more": false,
  "cursor_reset": false
}
```

`entries` is ordered in source-file order and never contains more than `limit` items. `next_cursor` is always returned, including at EOF, and is safe to use for the next poll. `cursor_reset` is `true` only when a valid cursor was discarded because the source file changed identity or was truncated; it is `false` for normal reads and an omitted cursor.

## Cursor semantics

The cursor is opaque to clients and must be treated as an uninterpreted string. The implementation may use a versioned, URL-safe authenticated/encoded representation. A cursor logically contains:

- a format version;
- an identity fingerprint for the selected file (at minimum device and inode, or an equivalent stable identity);
- a byte offset at the beginning of the next physical line to inspect.

The cursor advances over every scanned source line, including lines excluded by `level` or `pattern`. This prevents filtered polling from repeatedly rescanning or skipping data. The offset is committed only after a complete physical line has been read; a partial final line is left for the next poll.

A cursor from a different log file, a rotated file, or an offset greater than the current file size is stale. A stale-but-well-formed cursor resets to offset zero on the current file, returns entries from there, and sets `cursor_reset: true`. A file truncation (same identity, current size smaller than the cursor offset) has the same reset behavior. Resetting is explicit; the server must never silently continue from an unrelated offset.

A malformed, unsupported-version, tampered, or otherwise undecodable cursor is a client validation error (`400`, code `INVALID_CURSOR`), not an implicit reset. The response must not disclose cursor internals.

## Progression and `has_more`

The server scans forward from the cursor and stops after `limit` matching entries or EOF. `next_cursor` points after the last fully scanned physical line, not merely after the last returned entry. Therefore a response may contain fewer than `limit` entries when filters exclude lines.

`has_more` is `true` when the scan stopped because `limit` matching entries were returned and unread complete source lines remain. It is `false` at EOF, including when unread lines remain but none match the requested filters. This gives clients a stable polling rule: request again with `next_cursor` while `has_more` is true; otherwise retain `next_cursor` and poll later for appended data. A response at EOF does not advance the cursor.

The backend must bound the response by `limit`; it must not return the whole file just to evaluate a filter. Reading a partial final line does not count as an entry and does not set `has_more`.

## Deterministic validation and edge cases

- Missing `cursor`: begin at byte offset zero of the currently resolved file.
- Missing `limit`: use `100`.
- `limit` equal to `0`, negative, non-integer, or above `500`: return `400` with code `INVALID_LIMIT`; do not clamp.
- Missing `level` or `pattern`: do not apply that filter.
- Unsupported `level` (including lower-case spellings): return `400` with code `INVALID_LEVEL`.
- Invalid regular expression: return `400` with code `INVALID_PATTERN`; do not partially match or fall back to a literal search.
- Empty regular expression: valid and matches every message.
- End of file: return an empty or partial batch, `has_more: false`, and the current `next_cursor`.
- File rotation or truncation: reset as described above and mark `cursor_reset: true`.
- Concurrent append after the read begins: entries appended after the read's stable end are left for the next poll; they do not make the current response exceed `limit`.
- Concurrent disappearance or unreadable file: return the existing structured backend error convention; never expose a filesystem path or silently return an empty successful response.

Error responses are JSON objects with the plugin's normal structured error fields and a stable `code`. The codes above are part of the contract; error messages are explanatory and must not contain regex internals beyond what is safe or any secrets.

## Non-goals

This contract does not define follow mode, server push, SSE, WebSockets, log retention, log rotation policy, or lifecycle operations. It does not authorize changes to Mission Control, `odoo-tui`, databases, configuration, or process supervision.
