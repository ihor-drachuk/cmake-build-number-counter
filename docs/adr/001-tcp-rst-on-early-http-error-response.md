# ADR-001: TCP RST on early HTTP error responses

**Status:** Accepted
**Date:** 2026-03-14

## Context

Python's `http.server` (which our server is built on) does not drain the request body before sending error responses. This affects two scenarios:

1. **Oversized body (413):** `read_body()` checks `Content-Length` against `max_body_size` and rejects without reading the body.
2. **Rate limiting (429):** `check_rate_limit()` sends 429 in `do_POST` before `read_body()` is called.

In both cases the server sends a valid HTTP response and closes the socket. The OS kernel finds unread data in the receive buffer and sends a **TCP RST** instead of a graceful FIN. The client then gets `ConnectionAbortedError` (Windows) or `ConnectionResetError` (Linux) instead of the error response.

This is documented in RFC 9112 Section 9.6 ("Tear-down") and affects all HTTP servers that close connections with unread data. Production servers (Apache, Kestrel, Werkzeug) solve this with "lingering close" or body draining with timeouts.

## Decision

**Accept the behavior as-is in the server. Make tests tolerate both outcomes (HTTP error code or TCP reset).**

Rationale:

1. **Our server is not a general-purpose HTTP server.** It serves a single client (`client.py`) that never sends oversized requests and is never rate-limited in normal operation. These error paths are defenses against abuse, not normal control flow.

2. **Implementing proper body draining is disproportionate.** A correct implementation requires: drain with byte limit, drain with time limit (against slowloris), `shutdown(SHUT_WR)` for half-close, and platform-specific testing. This is essentially reimplementing what Apache/Kestrel do — wrong scope for a build counter.

3. **Production deployments should use a reverse proxy.** Nginx/Caddy/etc. handle body buffering, connection management, and TLS. They absorb this problem entirely.

4. **The server behavior is correct at the HTTP level.** It sends a valid error response. The TCP-level delivery failure is OS kernel behavior, not a server bug.

## Implementation

### Server side
`QuietHTTPServer` overrides `handle_error()` to silently ignore `ConnectionError` — the server does not crash or log noisy tracebacks when clients disconnect mid-response. This is the standard pattern used by Django, Werkzeug, and Prometheus (see `socketserver` docs: `handle_error()` "may be overridden").

### Test side
A helper `_expect_rejection(func, ...)` wraps calls that may trigger server rejection. It returns the normal `(status, data)` tuple, or a `_SERVER_REJECTED` sentinel if the TCP connection was reset. Tests then:
- If `_SERVER_REJECTED`: pass (server rejected the request, TCP didn't deliver the response — acceptable)
- Otherwise: assert on the HTTP status code and response body as usual

This is applied to all tests where the server responds before reading the request body: `TestContentLengthLimit` (413) and `TestRateLimiting` (429).

## Consequences

- Tests are deterministic: both outcomes (HTTP error delivered vs TCP reset) are valid and expected.
- No changes to `post_json`, `get_json`, or `_post_raw` helpers — the tolerance is in the test logic, not the transport layer.
- If we ever need reliable error delivery for rejected requests, the path forward is body draining with limits (see "Alternatives considered").

## Alternatives considered

### Body draining with limits
Read and discard up to N bytes (e.g. 64KB) with a timeout before sending the error response. This is what Werkzeug's dev server and Kestrel do.

**Rejected because:** introduces slowloris attack surface (must also add timeout handling), and our only client never triggers this path. A malicious client sending `Content-Length: 999999999` and trickling 1 byte/sec would tie up the server thread for the drain timeout duration.

### Lingering close (RFC 9112 §9.6)
`shutdown(SHUT_WR)` after sending response, then read with timeout, then `close()`. This is what Apache does.

**Rejected because:** even more complex, requires socket-level manipulation outside `http.server`'s abstraction, and doesn't compose well with `BaseHTTPRequestHandler`.

### Mark tests as platform-skipped
`@pytest.mark.skipif(sys.platform == 'win32')` on affected tests.

**Rejected because:** the problem exists on all platforms (just more frequent on Windows). Skipping hides the behavior rather than documenting it.

### Patch the client helpers (`post_json`, `_post_raw`)
Catch `ConnectionError` inside the helpers and return a synthetic error response.

**Rejected because:** these helpers are also used by tests that expect successful responses. Silently swallowing connection errors in the transport layer would mask real bugs. The tolerance belongs in the test logic where the intent is clear.
