# Troubleshooting

## Build number doesn't increment

- Verify Python is in PATH: `python --version`
- Check CMake build output for errors
- Test the client manually: `python src/client.py --project-key test`

## Server connection fails

- Verify server is running: `curl http://your-server:8080/`
- Check firewall rules and `BUILD_SERVER_URL` value
- The client falls back to local counter automatically on transient network errors

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
