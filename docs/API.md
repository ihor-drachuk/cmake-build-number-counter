# API Reference

## CMake Function

### `increment_build_number()`

Auto-increments a build number. Supports two modes: **BUILD** (default, runs at build time) and **CONFIGURE** (runs at configure time).

```cmake
increment_build_number(
    PROJECT_KEY <key>               # Required: unique project identifier
    [VERSION_HEADER <path>]         # Output header file path (required in BUILD mode)
    [MODE <BUILD|CONFIGURE>]        # Optional: BUILD (default) or CONFIGURE
    [OUTPUT_VARIABLE <var>]         # Optional: variable to receive build number (CONFIGURE mode)
    [SERVER_URL <url>]              # Optional: server URL (overrides BUILD_SERVER_URL env var)
    [SERVER_TOKEN <token>]          # Optional: API token (overrides BUILD_SERVER_TOKEN env var)
    [LOCAL_FILE <path>]             # Optional: local counter file (default: ${CMAKE_BINARY_DIR}/build_number.txt)
    [TARGET <name>]                 # Optional: custom target name (BUILD mode only)
    [FORCE_VERSION <N>]             # Optional: force-set to N instead of incrementing
    [NO_INCREMENT]                  # Optional: read current counter without incrementing
    [QUIET]                         # Optional: suppress log messages
)
```

**Key points:**
- In BUILD mode: call **after** `project()`, generates `VERSION_HEADER` at build time via custom target
- In CONFIGURE mode: can be called **before** `project()` with `OUTPUT_VARIABLE` — useful for embedding the build number in `project(VERSION ...)`
- Build number increments on every `cmake --build` in both modes
- `NO_INCREMENT` and `FORCE_VERSION` are mutually exclusive

### BUILD Mode (default)

The original behavior. Creates a CMake custom target that runs on every `cmake --build`.

- Requires `project(VERSION ...)` to have been called
- `VERSION_HEADER` is required
- Generates a C++ header with `APP_VERSION_*` defines

```cmake
project(MyApp VERSION 1.2.3.0)  # Last component = 0

increment_build_number(
    PROJECT_KEY "myapp"
    VERSION_HEADER "${CMAKE_BINARY_DIR}/generated/version.h"
)
```

### Configure Mode

Runs the increment at configure time via `execute_process()`. Automatically forces reconfigure on every `cmake --build` (via a stamp file that is deleted at build time, triggering reconfigure on next build), so the build number still increments on every build.

**Primary use case:** embedding the build number in `project(VERSION ...)`:

```cmake
include(CMakeBuildNumber)

increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "myapp"
    OUTPUT_VARIABLE BUILD_NUM
)

project(MyApp VERSION 1.2.3.${BUILD_NUM} LANGUAGES CXX)
# Now PROJECT_VERSION_TWEAK contains the build number
```

**Constraints:**
- `VERSION_HEADER` requires `PROJECT_VERSION` (call after `project()`)
- `OUTPUT_VARIABLE` works before `project()`
- `TARGET` is ignored (no custom target created)

### NO_INCREMENT

Reads the current counter value without incrementing or making any server calls. Requires the counter file to already exist (a prior normal increment must have run).

**Primary use case:** generating a VERSION_HEADER after `project()` when the build number was obtained before `project()`:

```cmake
# Step 1: get build number before project()
increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "myapp"
    OUTPUT_VARIABLE BUILD_NUM
)

project(MyApp VERSION 1.2.3.${BUILD_NUM} LANGUAGES CXX)

# Step 2: generate header using the same value (no double increment)
increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "myapp"
    VERSION_HEADER "${CMAKE_BINARY_DIR}/generated/version.h"
    NO_INCREMENT
)
```

Works in both BUILD and CONFIGURE modes. Mutually exclusive with `FORCE_VERSION`.

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
