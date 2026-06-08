"""WebSocket message type constants and helpers for the B↔C tunnel protocol."""

MSG_REQUEST = "request"
MSG_RESPONSE = "response"
MSG_STREAM_CHUNK = "stream_chunk"
MSG_STREAM_END = "stream_end"
MSG_PING = "ping"
MSG_PONG = "pong"

# Header allowlist forwarded from B/X to C.
# DO NOT loosen — tested by test_header_allowlist.
ALLOWED_HEADERS = frozenset(
    ("authorization", "x-api-key", "content-type", "anthropic-version", "anthropic-beta")
)
