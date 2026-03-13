# Build Number Counter

Automatic build number incrementing for CMake projects. Supports a central server for team synchronization with offline fallback.

## Quick Start

### Prerequisites

- **Python 3**
- **CMake 3.20+**

### 1. Add to Your CMake Project

#### Option A: FetchContent (recommended)

```cmake
include(FetchContent)
FetchContent_Declare(
    build_number_counter
    GIT_REPOSITORY https://github.com/ihor-drachuk/build-number-counter.git
    GIT_TAG main
)
FetchContent_MakeAvailable(build_number_counter)

project(MyApp VERSION 1.2.3.0 LANGUAGES CXX)  # Last component = 0

list(APPEND CMAKE_MODULE_PATH "${build_number_counter_SOURCE_DIR}/src")
include(CMakeBuildNumber)

increment_build_number(
    PROJECT_KEY "myapp"
    VERSION_HEADER "${CMAKE_BINARY_DIR}/generated/version.h"
)

add_executable(myapp main.cpp)
target_include_directories(myapp PRIVATE ${CMAKE_BINARY_DIR}/generated)
```

#### Option B: Manual copy

Copy `src/CMakeBuildNumber.cmake` and `src/client.py` to your project, then:

```cmake
project(MyApp VERSION 1.2.3.0 LANGUAGES CXX)

include(${CMAKE_SOURCE_DIR}/cmake/CMakeBuildNumber.cmake)

increment_build_number(
    PROJECT_KEY "myapp"
    VERSION_HEADER "${CMAKE_BINARY_DIR}/generated/version.h"
)

add_executable(myapp main.cpp)
target_include_directories(myapp PRIVATE ${CMAKE_BINARY_DIR}/generated)
```

### 2. Use the Generated Header

```cpp
#include <iostream>
#include "version.h"

int main() {
    std::cout << "Version: " << APP_VERSION_STRING << std::endl;
    std::cout << "Build:   " << APP_VERSION_BUILD << std::endl;
}
```

### 3. Set Up Server (optional)

For synchronized build numbers across machines:

```bash
cd build-number-counter/src
python server.py --port 8080 --accept-unknown
```

On client machines, set the environment variable:
```bash
export BUILD_SERVER_URL=http://your-server:8080   # Linux/Mac
set BUILD_SERVER_URL=http://your-server:8080      # Windows
```

### 4. Build

```bash
cmake -B build
cmake --build build   # build number increments here
```

## API Reference

### `increment_build_number()`

Generates a version header with an auto-incremented build number at **build time**.

```cmake
increment_build_number(
    PROJECT_KEY <key>           # Required: unique project identifier
    VERSION_HEADER <path>       # Required: output header file path
    [SERVER_URL <url>]          # Optional: server URL (overrides BUILD_SERVER_URL env var)
    [SERVER_TOKEN <token>]      # Optional: API token (overrides BUILD_SERVER_TOKEN env var)
    [LOCAL_FILE <path>]         # Optional: local counter file (default: ${CMAKE_BINARY_DIR}/build_number.txt)
    [TARGET <name>]             # Optional: custom target name
    [FORCE_VERSION <N>]         # Optional: force-set to N instead of incrementing
    [QUIET]                     # Optional: suppress log messages
)
```

**Key points:**
- Call **after** `project()` — takes MAJOR, MINOR, PATCH from `PROJECT_VERSION`
- Build number increments at **build time** (via custom target), not at configure time
- Set `VERSION` last component to `0` (e.g., `VERSION 1.2.3.0`) — the build number replaces it in the header

### Generated Header

The function generates a header file (at the path specified by `VERSION_HEADER`) with:

```cpp
#pragma once

#define APP_VERSION_MAJOR 1
#define APP_VERSION_MINOR 2
#define APP_VERSION_PATCH 3
#define APP_VERSION_BUILD 42       // auto-incremented
#define APP_VERSION_STRING "1.2.3.42"
```

### Multi-project Setup

Each project gets its own independent counter:

```cmake
project(Frontend VERSION 3.2.1.0)
increment_build_number(
    PROJECT_KEY "frontend"
    VERSION_HEADER "${CMAKE_BINARY_DIR}/generated/version_frontend.h"
)

project(Backend VERSION 7.0.5.0)
increment_build_number(
    PROJECT_KEY "backend"
    VERSION_HEADER "${CMAKE_BINARY_DIR}/generated/version_backend.h"
)
```

### Client Script (standalone)

The Python client can be used independently outside of CMake:

```bash
python src/client.py --project-key myproject
python src/client.py --project-key myproject --server-url http://localhost:8080
python src/client.py --project-key myproject --server-token <token>
python src/client.py --project-key myproject --local-file ./counter.txt
python src/client.py --project-key myproject --output-format json
python src/client.py --project-key myproject --quiet

# Force-set build number (no increment)
python src/client.py --project-key myproject --force-version 42
python src/client.py --project-key myproject --force-version 0  # reset
```

Output formats: `plain` (default), `cmake`, `json`.

### Server

```bash
python src/server.py                          # default: port 8080, reject unknown projects
python src/server.py --accept-unknown         # auto-approve new projects
python src/server.py --port 9000 --host 127.0.0.1
python src/server.py --data-dir /var/lib/build-counter  # custom data directory
python src/server.py --max-body-size 2048     # max request body size (default: 1024 bytes)
python src/server.py --max-projects 50        # max project count (default: 100, 0 = unlimited)
python src/server.py --rate-limit 5           # max requests per minute per IP (default: 10, 0 = off)
python src/server.py --ban-duration 1800      # temp ban duration in seconds (default: 600)
python src/server.py --ban-permanent          # use persistent bans instead of temporary

# Token management
python src/server.py --add-token --token-name "ci" --token-projects "my-app,my-lib"
python src/server.py --add-token --token-name "admin" --token-admin
python src/server.py --list-tokens
python src/server.py --remove-token "ci"

# Counter management (offline, no server started)
python src/server.py --set-counter --project-key myproject --version 42
python src/server.py --set-counter --project-key myproject --version 0 --data-dir /var/lib/build-counter
```

**Endpoints:**
- `GET /` — service info
- `POST /increment` — increment and return build number (JSON body: `{"project_key": "...", "local_version": N}`)
- `POST /set` — force-set build number to exact value (JSON body: `{"project_key": "...", "version": N}`)

**Data file:** `server-data/build_numbers.json`

```json
{
  "myproject": 42,
  "another-project": 15
}
```

**Project key format:**
- Must match `[a-zA-Z0-9._-]`, 1-128 characters
- Examples: `my-app`, `com.example.project`, `BuildServer_v2`
- Invalid keys are rejected with 400 (server), non-zero exit (client), FATAL_ERROR (CMake)

**Project approval:**
- A project key present in the file is approved
- Missing key + `--accept-unknown` flag = auto-added starting at 1
- Missing key without the flag = rejected (403)
- With `--accept-unknown`, max project count is enforced (default: 100, `--max-projects 0` for unlimited)

To add/reset a project manually: stop the server, edit the JSON, restart.

## How It Works

**Server available:** client contacts server, server increments atomically and returns the new number, local file is updated to match.

**Server unavailable:** client increments local counter, saves sync state for later.

**Reconnection:** on next successful server contact, client sends its local counter; server uses the higher value. All machines converge.

## Configuration

### Server URL Priority

1. `SERVER_URL` parameter in CMakeLists.txt
2. `BUILD_SERVER_URL` environment variable
3. `--server-url` flag (for direct client.py calls)
4. No server — local counter only

### Server Token Priority

1. `SERVER_TOKEN` parameter in CMakeLists.txt
2. `BUILD_SERVER_TOKEN` environment variable
3. `--server-token` flag (for direct client.py calls)
4. No token — unauthenticated request

### Local Counter Location

Default: `${CMAKE_BINARY_DIR}/build_number.txt`

Custom:
```cmake
increment_build_number(
    PROJECT_KEY "myapp"
    VERSION_HEADER "${CMAKE_BINARY_DIR}/generated/version.h"
    LOCAL_FILE "${CMAKE_SOURCE_DIR}/build_counter.txt"
)
```

## Deployment

### Linux (systemd)

Create `/etc/systemd/system/build-counter.service`:
```ini
[Unit]
Description=Build Number Counter Server
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/path/to/build-counter
ExecStart=/usr/bin/python3 /path/to/build-counter/src/server.py --port 8080 --accept-unknown
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable build-counter
sudo systemctl start build-counter
```

### Windows (NSSM)

Using [NSSM](https://nssm.cc/download):

```cmd
nssm install BuildCounterServer "C:\Python39\python.exe" "C:\path\to\src\server.py --port 8080 --accept-unknown"
nssm set BuildCounterServer AppDirectory "C:\path\to\build-counter"
nssm start BuildCounterServer
```

## Security

### Authentication (optional)

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

### Rate Limiting

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

### Built-in protections
- **Rate limiting** — `--rate-limit N` per IP (default: 10 req/min), with temp or permanent ban
- **Request size limit** — rejects bodies over `--max-body-size` (default: 1 KB) with 413
- **Project count limit** — caps auto-created projects at `--max-projects` (default: 100) with 507
- **Input validation** — project keys must match `[a-zA-Z0-9._-]`, 1-128 chars

For external access, additionally consider:
- Use HTTPS (via reverse proxy)
- Restrict the approved projects list (don't use `--accept-unknown`)

## Troubleshooting

**Build number doesn't increment**
- Verify Python is in PATH: `python --version`
- Check CMake build output for errors
- Test the client manually: `python src/client.py --project-key test`

**Server connection fails**
- Verify server is running: `curl http://your-server:8080/`
- Check firewall rules and `BUILD_SERVER_URL` value
- The client falls back to local counter automatically

**Project key not approved**
- Add it to `server-data/build_numbers.json`: `"your-project": 0`
- Or restart the server with `--accept-unknown`

**429 Too Many Requests from server**
- Your IP has been rate-limited or banned
- Temporary ban: wait for expiry (default 10 min) or restart server
- Permanent ban: remove your IP from `server-data/banned_ips.json`
- The client falls back to local counter automatically

**401 Unauthorized from server**
- Check that the token is correct: `python src/server.py --list-tokens`
- Verify the token has access to the project key (check project patterns)
- If using environment variable, verify `BUILD_SERVER_TOKEN` is set
- The client falls back to local counter on auth failure

**Build numbers diverged across machines**
- The sync mechanism resolves this on next server connection
- Use `--force-version` on the client or `POST /set` on the server to reset
- Or use `--set-counter` on the server CLI for offline correction
