# Troubleshooting

## Build number doesn't increment

- Verify Python is in PATH: `python --version`
- Check CMake build output for errors
- Test the client manually: `python src/client.py --project-key test`

## Server connection fails

- Verify server is running: `curl http://your-server:8080/`
- Check firewall rules and `BUILD_SERVER_URL` value
- The client falls back to local counter automatically

## Project key not approved

- Add it to `server-data/build_numbers.json`: `"your-project": 0`
- Or restart the server with `--accept-unknown`

## 429 Too Many Requests from server

- Your IP has been rate-limited or banned
- Temporary ban: wait for expiry (default 10 min) or restart server
- Permanent ban: remove your IP from `server-data/banned_ips.json`
- The client falls back to local counter automatically

## 401 Unauthorized from server

- Check that the token is correct: `python src/server.py --list-tokens`
- Verify the token has access to the project key (check project patterns)
- If using environment variable, verify `BUILD_SERVER_TOKEN` is set
- The client falls back to local counter on auth failure

## Build numbers diverged across machines

- The sync mechanism resolves this on next server connection
- Use `--force-version` on the client or `POST /set` on the server to reset
- Or use `--set-counter` on the server CLI for offline correction
