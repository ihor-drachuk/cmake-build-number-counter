# Build Number Counter (CBNC)

[![CI](https://github.com/ihor-drachuk/cmake-build-number-counter/actions/workflows/ci.yml/badge.svg)](https://github.com/ihor-drachuk/cmake-build-number-counter/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Automatic build number tracking for CMake projects.
Every `cmake --build` increments the counter and generates a C++ version header — zero config, no dependencies.

## Features

- Auto-increments build number on every build
- Generates `version.h` with `MAJOR.MINOR.PATCH.BUILD` defines
- **Two modes:** build-time (default) or configure-time (for `project(VERSION ...)`)
- Works locally out of the box — no server needed
- Optional central server for team synchronization
- Offline fallback with automatic reconnection and sync
- Token-based authentication, rate limiting, IP banning
- No external dependencies (Python stdlib only)
- Multi-project support with independent counters
- Force-set build number for resets and migrations

## Supported Generators

| Generator | Platforms |
|-----------|-----------|
| Unix Makefiles | Linux, macOS |
| Ninja | Linux, macOS, Windows |
| Visual Studio 17 (2022) | Windows |

## Quick Start

**Prerequisites:** Python 3, CMake 3.20+

### 1. Add to your CMakeLists.txt

```cmake
cmake_minimum_required(VERSION 3.20)
project(MyApp VERSION 1.2.3.0 LANGUAGES CXX)  # Last component = 0

# ---- CBNC: Build Number Counter ----
include(FetchContent)
FetchContent_Declare(
    build_number_counter
    GIT_REPOSITORY https://github.com/ihor-drachuk/cmake-build-number-counter.git
    GIT_TAG main
)
FetchContent_MakeAvailable(build_number_counter)

list(APPEND CMAKE_MODULE_PATH "${build_number_counter_SOURCE_DIR}/src")
include(CMakeBuildNumber)

increment_build_number(
    PROJECT_KEY "myapp"
    VERSION_HEADER "${CMAKE_BINARY_DIR}/generated/version.h"
)
# ---- End CBNC ----

add_executable(myapp main.cpp)
target_include_directories(myapp PRIVATE ${CMAKE_BINARY_DIR}/generated)
add_dependencies(myapp generate_version_myapp)  # target name = generate_version_{PROJECT_KEY}
```

### 2. Use in your code

```cpp
#include "version.h"
#include <iostream>

int main() {
    std::cout << "Version: " << APP_VERSION_STRING << std::endl;
    // Output: "Version: 1.2.3.42"
}
```

The generated header provides:

```cpp
#define APP_VERSION_MAJOR  1
#define APP_VERSION_MINOR  2
#define APP_VERSION_PATCH  3
#define APP_VERSION_BUILD  42          // auto-incremented
#define APP_VERSION_STRING "1.2.3.42"
```

### 3. Build

```bash
cmake -B build
cmake --build build     # build number increments here!
cmake --build build     # ...and here (43, 44, ...)
```

That's it! No server, no setup — just build.

### Alternative: Configure Mode

Need the build number in `project(VERSION ...)`? Use configure mode — call **before** `project()`:

```cmake
cmake_minimum_required(VERSION 3.20)

# ---- CBNC: Build Number Counter (Configure Mode) ----
include(FetchContent)
FetchContent_Declare(build_number_counter
    GIT_REPOSITORY https://github.com/ihor-drachuk/cmake-build-number-counter.git
    GIT_TAG main)
FetchContent_MakeAvailable(build_number_counter)

list(APPEND CMAKE_MODULE_PATH "${build_number_counter_SOURCE_DIR}/src")
include(CMakeBuildNumber)

increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "myapp"
    OUTPUT_VARIABLE BUILD_NUM
)
# ---- End CBNC ----

project(MyApp VERSION 1.2.3.${BUILD_NUM} LANGUAGES CXX)
# PROJECT_VERSION = "1.2.3.42", PROJECT_VERSION_TWEAK = 42
```

The build number auto-increments on every `cmake --build` (auto-reconfigure). See [API Reference](docs/API.md#configure-mode) for details.

## How It Works

Build number increments on every `cmake --build`:

- **Build mode** (default) — custom target runs at build time, generates `version.h`
- **Configure mode** — runs at configure time, returns value via `OUTPUT_VARIABLE` for use in `project(VERSION ...)`. Auto-triggers reconfigure on each build.

Both modes support server sync:
- **No server** — counter stored in a local text file
- **Server available** — counter incremented atomically on the server
- **Server goes down** — seamless fallback to local, auto-sync when it comes back

## Team/CI Synchronization (optional)

For shared build numbers across machines, add `SERVER_URL` to your CMakeLists.txt:

```cmake
increment_build_number(
    PROJECT_KEY "myapp"
    VERSION_HEADER "${CMAKE_BINARY_DIR}/generated/version.h"
    SERVER_URL "https://cbnc-server.net"      # ← public server, free, no auth
)
```

Or set an environment variable (no CMake changes needed):

```bash
export BUILD_SERVER_URL=https://cbnc-server.net   # Linux/Mac
set BUILD_SERVER_URL=https://cbnc-server.net      # Windows
```

> **Public server** — a free community server at `https://cbnc-server.net` is available for anyone to use, no registration or tokens required. To avoid collisions, use a globally unique project key like `mycompany-myapp-a3f8b2c91d4e` (org + project + random suffix). For private use, you can [self-host](docs/SERVER.md#self-hosting) your own server.

That's all — CMake picks it up automatically. See [Server Guide](docs/SERVER.md) for deployment, authentication, and configuration.

## Examples

See the [examples/](examples/) directory for complete, runnable projects:

| Example | What it shows |
|---------|---------------|
| [1-simple](examples/1-simple/) | Minimal setup, local counter only |
| [2-with-server](examples/2-with-server/) | Synchronized counter via central server |
| [3-custom-location](examples/3-custom-location/) | Counter file in source dir (for VCS tracking) |
| [4-configure-mode](examples/4-configure-mode/) | Build number in `project(VERSION ...)` at configure time |

## Documentation

| Document | Contents |
|----------|----------|
| [API Reference](docs/API.md) | CMake function parameters, client CLI, configuration priority |
| [Server Guide](docs/SERVER.md) | Setup, CLI flags, endpoints, deployment (systemd/NSSM) |
| [Security](docs/SECURITY.md) | Authentication, rate limiting, hardening |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common issues and solutions |
| [Contributing](docs/CONTRIBUTING.md) | Architecture, dev setup, running tests |
| [Test Coverage](docs/TEST-COVERAGE.md) | Feature-by-feature test status |

## FAQ

**Build number doesn't increment?** Check that Python is in PATH and look at CMake output for errors. See [Troubleshooting](docs/TROUBLESHOOTING.md).

**Server connection fails?** The client falls back to local counter automatically. Check `BUILD_SERVER_URL` and firewall rules.

## License

[MIT](LICENSE)
