# Contributing

Guide for developers working on the cmake-build-number-counter project.

## Project Overview

Automatic build number incrementing system for CMake projects. Three components:

- **Server** (`src/server.py`) — HTTP server managing per-project counters with atomic increments and persistent JSON storage
- **Client** (`src/client.py`) — fetches next build number from server, falls back to local file counter when offline, syncs back on reconnect
- **CMake module** (`src/CMakeBuildNumber.cmake`) — integrates the client into CMake builds. Two modes: BUILD (custom target at build time) and CONFIGURE (execute_process at configure time with auto-reconfigure). Creates `cbnc::version` INTERFACE library target for easy consumption via `target_link_libraries()`

## Architecture

```
Developer's machine              Build server
┌──────────────────┐           ┌──────────────┐
│ CMakeLists.txt   │           │ server.py    │
│   └─ increment_  │           │   └─ /incr.  │
│      build_num() │           │      endpoint│
│      ↓           │  HTTP     │      ↓       │
│ CMake custom     │ ──────►   │ build_numbers│
│ target (build    │ ◄──────   │ .json        │
│ time)            │           └──────────────┘
│   └─ client.py   │
│      ↓           │
│ cbnc-version.h   │
│ build_number.txt │
└──────────────────┘
```

**Data flow:**
1. `cmake --build` triggers the custom target
2. Custom target runs `client.py` via Python
3. Client tries server → falls back to local file → returns build number
4. CMake script writes `cbnc-version.h` with the number

**Force-set flow:** `POST /set` or `--force-version` / `--set-counter` bypasses increment and sets the counter to an exact value. Used for resets, migrations, and disaster recovery.

**Offline sync:** client saves a `.sync` file when working locally. On next successful server contact, it sends its local counter; server takes the max.

## Project Structure

```
cmake-build-number-counter/
├── src/
│   ├── CMakeBuildNumber.cmake   # CMake module (increment_build_number function)
│   ├── client.py                # Python client (server + local fallback)
│   └── server.py                # Python HTTP server
├── tests/
│   ├── conftest.py              # Shared fixtures (temp files, in-process server)
│   ├── test_client.py           # Unit tests for client.py
│   ├── test_server.py           # Unit tests for server.py (in-process HTTP)
│   ├── test_integration.py      # End-to-end: subprocess server + client
│   └── test_cmake.py            # CMake build verification
├── examples/
│   ├── 1-simple/                # Basic usage
│   ├── 2-with-server/           # Server-synced
│   ├── 3-custom-location/       # Custom counter file path
│   └── 4-configure-mode/        # Configure-time build number
├── .github/workflows/test.yml   # CI: Python tests + CMake tests
├── pyproject.toml               # pytest config
├── requirements-dev.txt         # Dev dependencies (pytest)
├── README.md                    # User-facing documentation
└── LICENSE                      # MIT
```

## Setup

### Prerequisites

- Python 3.9+
- CMake 3.20+ and a C++ compiler (for CMake tests and examples)

### Install dev dependencies

```bash
python -m venv .venv

# Linux/Mac
source .venv/bin/activate

# Windows
.venv\Scripts\activate

pip install -r requirements-dev.txt
```

## Running Tests

```bash
# All tests
python -m pytest tests/ -v

# Only Python tests (skip CMake)
python -m pytest tests/ -v -m "not cmake"

# Only CMake tests
python -m pytest tests/ -v -m cmake

# Single test file
python -m pytest tests/test_client.py -v
```

Tests take ~45 seconds total. Integration tests start real server subprocesses on random ports; CMake tests configure and build example projects in temp directories.

**Testing rule:** increment tests must verify at least 2 consecutive increments via **separate cmake invocations** (2 separate `cmake --build` or 2 separate `cmake configure` runs). A single increment (0→1) is not sufficient — always confirm the second value equals the first plus one. Do not just call `increment_build_number()` twice in one CMakeLists.txt; that tests in-process behavior, not real usage.

**Examples and docs rule:** every example project in `examples/` and every CMake snippet in documentation must be manually verified after creation or editing — configure, build, and run to confirm it actually works. Code snippets in README/API docs must be consistent with real examples.

**Coverage map:** see [TEST-COVERAGE.md](TEST-COVERAGE.md) for a feature-by-feature test status table. Update it when adding new features or tests.

## Running Locally

### Server

```bash
python src/server.py --accept-unknown
python src/server.py --port 9000 --data-dir ./my-data
```

### Client (standalone)

```bash
python src/client.py --project-key test --quiet
python src/client.py --project-key test --server-url http://localhost:8080
```

### CMake example

```bash
cd examples/1-simple
cmake -B build
cmake --build build       # increments build number
cmake --build build       # increments again
./build/simple_example    # or .\build\Debug\simple_example.exe on Windows
```

## Key Design Decisions

- **Two modes, same behavior.** BUILD mode uses `add_custom_target(... ALL)`, CONFIGURE mode uses `execute_process()` + auto-reconfigure via a stamp file (created at configure, deleted by custom target at build time, triggering reconfigure on next build). Both increment on every `cmake --build`.
- **No external dependencies.** Both client and server use only Python stdlib. pytest is the only dev dependency.
- **Atomic file writes.** Both client and server use temp file + `os.replace()` to avoid partial writes.
- **Server is optional.** Everything works locally without a server; the server adds cross-machine synchronization.
- **Force-set uses the same auth as increment.** Any token with access to a project can both `/increment` and `/set` it. The server CLI `--set-counter` bypasses `accept_unknown` because it is a local admin operation.
