# API Reference

## CMake Function

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

### Installation Without FetchContent

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

## Client CLI

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

## Configuration Priority

### Server URL

1. `SERVER_URL` parameter in CMakeLists.txt
2. `BUILD_SERVER_URL` environment variable
3. `--server-url` flag (for direct client.py calls)
4. No server — local counter only

### Server Token

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
