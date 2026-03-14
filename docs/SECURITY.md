# Security

## Authentication (optional)

Token-based authentication is available. When enabled, every `POST` request must include a valid `Authorization: Bearer <token>` header.

**Setup:**
```bash
# Create a token for specific projects
python src/server.py --add-token --token-name "ci-pipeline" --token-projects "my-app,my-lib"

# Create an admin token (access to all projects)
python src/server.py --add-token --token-name "admin" --token-admin

# Wildcard patterns
python src/server.py --add-token --token-name "org" --token-projects "my-org-*"

# List / remove tokens
python src/server.py --list-tokens
python src/server.py --remove-token "ci-pipeline"
```

**Client configuration:**
```bash
# Via CLI flag
python src/client.py --project-key my-app --server-token <token>

# Via environment variable
export BUILD_SERVER_TOKEN=<token>

# Via CMake parameter
increment_build_number(
    PROJECT_KEY "my-app"
    VERSION_HEADER "${CMAKE_BINARY_DIR}/generated/version.h"
    SERVER_URL "http://server:8080"
    SERVER_TOKEN "${BUILD_SERVER_TOKEN}"
)
```

Auth is **disabled by default** — if no tokens are configured (`tokens.json` absent or empty), all requests are allowed. `GET /` is always unauthenticated.

## Rate Limiting

Per-IP rate limiting is **enabled by default** (10 requests/minute). When exceeded, the IP is temporarily banned (default: 10 minutes). All requests during ban get `429 Too Many Requests`.

```bash
# Defaults: 10 req/min, 10-minute temp ban
python src/server.py

# Custom rate and ban duration
python src/server.py --rate-limit 5 --ban-duration 1800

# Permanent bans (persisted to banned_ips.json, survive restart)
python src/server.py --rate-limit 10 --ban-permanent

# Disable rate limiting (trusted network)
python src/server.py --rate-limit 0
```

**Unbanning:**
- Temporary bans expire automatically, or restart the server
- Permanent bans: edit `server-data/banned_ips.json` and remove the IP entry (picked up on next request)

**Note:** The client automatically falls back to local counter when rate-limited (429), so builds are not blocked.

## Built-in Protections

- **Rate limiting** — `--rate-limit N` per IP (default: 10 req/min), with temp or permanent ban
- **Request size limit** — rejects bodies over `--max-body-size` (default: 1 KB) with 413
- **Project count limit** — caps auto-created projects at `--max-projects` (default: 100) with 507
- **Input validation** — project keys must match `[a-zA-Z0-9._-]`, 1-128 chars

## Recommendations for Production

- Use HTTPS (via reverse proxy such as nginx or Caddy)
- Restrict the approved projects list (don't use `--accept-unknown`)
- Enable token authentication
- Run the server behind a firewall or VPN
