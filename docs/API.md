# API Reference

## CMake Function

### `increment_build_number()`

Auto-increments a build number. Supports two modes: **BUILD** (default, runs at build time) and **CONFIGURE** (runs at configure time).

```cmake
increment_build_number(
    PROJECT_KEY <key>               # Required: unique project identifier
    [MODE <BUILD|CONFIGURE>]        # Optional: BUILD (default) or CONFIGURE
    [OUTPUT_VARIABLE <var>]         # Optional: variable to receive build number (CONFIGURE mode)
    [SERVER_URL <url>]              # Optional: server URL (overrides BUILD_SERVER_URL env var)
    [SERVER_TOKEN <token>]          # Optional: API token (overrides BUILD_SERVER_TOKEN env var)
    [LOCAL_FILE <path>]             # Optional: local counter file (default: ${CMAKE_BINARY_DIR}/build_number.txt)
    [TARGET <name>]                 # Optional: custom target name (BUILD mode only)
    [FORCE_VERSION <N>]             # Optional: force-set to N instead of incrementing
    [REUSE_COUNTER]                 # Optional: read current counter without incrementing (two-step pattern)
    [DISABLED]                      # Optional: skip incrementing if build artifacts already exist
    [QUIET]                         # Optional: suppress log messages
)
```

**Key points:**
- Automatically generates `${CMAKE_BINARY_DIR}/cbnc-generated/cbnc-version.h` with version defines
- Creates `cbnc::version` INTERFACE library — use `target_link_libraries(myapp PRIVATE cbnc::version)` to consume
- In BUILD mode: call **after** `project()`, generates header at build time via custom target
- In CONFIGURE mode: can be called **before** `project()` with `OUTPUT_VARIABLE`; header is auto-generated when `PROJECT_VERSION` is set
- Build number increments on every `cmake --build` in both modes
- `REUSE_COUNTER` and `FORCE_VERSION` are mutually exclusive

### BUILD Mode (default)

Creates a CMake custom target that runs on every `cmake --build`.

- Requires `project(VERSION ...)` to have been called
- Generates `cbnc-version.h` with `APP_VERSION_*` defines
- Creates `cbnc::version` target with include directory and build dependency

```cmake
project(MyApp VERSION 1.2.3.0)  # Last component = 0

increment_build_number(
    PROJECT_KEY "myapp"
)

add_executable(myapp main.cpp)
target_link_libraries(myapp PRIVATE cbnc::version)
```

Any target that links `cbnc::version` will find `cbnc-version.h` in its include path and will wait for the header to be generated before compiling.

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
- `OUTPUT_VARIABLE` works before `project()`
- `cbnc-version.h` is auto-generated when `PROJECT_VERSION` is set (after `project()`)
- `TARGET` is ignored (no custom target created)

### REUSE_COUNTER

Reads the current counter value from the local file without incrementing or making any server calls. Requires the counter file to already exist (a prior normal increment must have run).

This is a utility flag for the **two-step CONFIGURE mode pattern**: increment before `project()`, then `REUSE_COUNTER` after `project()` to generate the header without double-incrementing.

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
    REUSE_COUNTER
)
# cbnc-version.h is now generated, cbnc::version target is available
```

Works in both BUILD and CONFIGURE modes. Mutually exclusive with `FORCE_VERSION`.

### DISABLED

Skips incrementing when build artifacts already exist. Designed for IDE/debug presets where reconfigure and server calls are unwanted overhead.

**Behavior:**
- Counter file + header both exist → skip everything, log the skipped command
- Counter file exists, header missing → generate header from counter (no client.py, no server call)
- No files exist (first run) → full bootstrap (runs normally as if DISABLED wasn't set, but without creating a stamp file / auto-reconfigure mechanism)

**Combinations:**
- `DISABLED` alone — behavior above
- `DISABLED + REUSE_COUNTER` — if header exists, skip; if not, generate from counter
- `DISABLED + FORCE_VERSION` — DISABLED is ignored, FORCE_VERSION runs (it's idempotent)

**Typical usage** with a CMake option controlled by IDE presets:

```cmake
option(MY_SKIP_INCREMENT "Skip build number increment (IDE/debug)" OFF)
if(MY_SKIP_INCREMENT)
    set(_CBNC_DISABLED DISABLED)
endif()

increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "myapp"
    OUTPUT_VARIABLE BUILD_NUM
    ${_CBNC_DISABLED}
)

project(MyApp VERSION 1.2.3.${BUILD_NUM} LANGUAGES CXX)

increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "myapp"
    REUSE_COUNTER
    ${_CBNC_DISABLED}
)
```

When the log says `[CBNC] Skipped: python .../client.py ...`, you can copy-paste that command to manually increment if needed.

### Generated Header

The function generates `${CMAKE_BINARY_DIR}/cbnc-generated/cbnc-version.h` with:

```cpp
#pragma once

#define APP_VERSION_MAJOR 1
#define APP_VERSION_MINOR 2
#define APP_VERSION_PATCH 3
#define APP_VERSION_BUILD 42       // auto-incremented
#define APP_VERSION_STRING "1.2.3.42"
```

### Installation Without FetchContent

Copy `src/CMakeBuildNumber.cmake` and `src/client.py` to your project, then:

```cmake
project(MyApp VERSION 1.2.3.0 LANGUAGES CXX)

include(${CMAKE_SOURCE_DIR}/cmake/CMakeBuildNumber.cmake)

increment_build_number(
    PROJECT_KEY "myapp"
)

add_executable(myapp main.cpp)
target_link_libraries(myapp PRIVATE cbnc::version)
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

### Exit codes

- **0** — success (build number printed to stdout)
- **1** — error: invalid project key, invalid `--force-version`, or **server rejection** (HTTP 401, 403, 429)

Server rejections (authentication failure, forbidden project, rate-limit ban) cause exit code 1 and a message to stderr. The `--quiet` flag does **not** suppress rejection errors. Transient failures (network errors, timeouts, HTTP 500) still fall back to the local counter silently.

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
    LOCAL_FILE "${CMAKE_SOURCE_DIR}/build_counter.txt"
)
```
