# Troubleshooting

## Build number doesn't increment

- Verify Python is in PATH: `python --version`
- Check CMake build output for errors
- Test the client manually: `python src/client.py --project-key test`

## Server connection fails

- Verify server is running: `curl http://your-server:8080/`
- Check firewall rules and `BUILD_SERVER_URL` value
- The client falls back to local counter automatically on transient network errors and prints a warning to stderr (visible even with `--quiet` / `QUIET`)

## Build fails with "Server rejected request"

Server rejections (HTTP 401, 403, 429) cause the build to fail immediately instead of silently falling back to a local counter. This prevents counter divergence from going unnoticed.

### HTTP 401 — Authentication failure

- Token is missing, invalid, or does not have access to the project
- Fix: check your token with `python src/server.py --list-tokens`
- Verify `SERVER_TOKEN` / `BUILD_SERVER_TOKEN` is set correctly
- Verify the token covers the project key (check project patterns)

### HTTP 403 — Project not approved

- The server runs in strict mode and the project key is not pre-registered
- Fix: add it to `server-data/build_numbers.json`: `"your-project": 0`
- Or restart the server with `--accept-unknown`

### HTTP 429 — Rate limited / banned

- Your IP has been rate-limited or banned
- Temporary ban: wait for expiry (default 10 min) or restart the server
- Permanent ban: remove your IP from `server-data/banned_ips.json`

> **Note:** If the 429 response cannot be delivered due to a TCP reset (see `docs/adr/001-tcp-rst-on-early-http-error-response.md`), the client treats it as a transient network error and falls back to local. This is an edge case, not the normal path.

## Build numbers diverged across machines

- The sync mechanism resolves this on next server connection
- Use `--force-version` on the client or `POST /set` on the server to reset
- Or use `--set-counter` on the server CLI for offline correction

## HTTP 408 — Request Timeout

- The server received headers but the body did not arrive within the
  socket timeout (3 s). Likely causes:
  - Slow / lossy network between client and server
  - A proxy buffering the body
  - A non-CBNC client probing the server (port scanner, health probe
    that opens a TCP connection without sending HTTP)
- The CBNC client itself sends the body in a single write, so it should
  not normally hit this.

## HTTP 503 — Server overloaded

- All worker threads (`--max-threads`) plus their queue are busy
- Healthy steady-state operation should never see this; sustained 503
  means either traffic exceeds capacity or workers are stuck
- Check `GET /healthz` — `queue_depth` shows the backlog
- Increase `--max-threads` if the load is real, otherwise investigate
  why workers are not draining (filesystem stalls, downstream calls)

## Watchdog killed the server (`os._exit(1)`)

If `--watchdog` is enabled and the server self-terminates, stderr
contains lines like:

```
[watchdog] /healthz error: timed out (3/3)
[watchdog] failure threshold reached, exiting via os._exit(1)
```

This means three consecutive `/healthz` probes failed. Common causes:
- All workers stuck (Slowloris bypassed timeout, downstream I/O hang)
- Filesystem unresponsive (for example, a stuck NFS mount)
- Severe overload — pool saturated for longer than `interval × failures`

The container orchestrator (Docker `restart_policy`, Railway, k8s) will
restart the process. If watchdog fires repeatedly:
- Lower `--max-threads` to reduce contention, or raise it if the load
  is legitimate
- Increase `--watchdog-interval` / `--watchdog-failures` for noisier
  environments (e.g. shared CI runners with bursty load)
- To disable entirely, drop the `--watchdog` flag
