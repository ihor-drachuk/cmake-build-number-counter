# Contributing

Guide for developers working on the build-number-counter project.

## Project Overview

Automatic build number incrementing system for CMake projects. Three components:

- **Server** (`src/server.py`) — HTTP server managing per-project counters with atomic increments and persistent JSON storage
- **Client** (`src/client.py`) — fetches next build number from server, falls back to local file counter when offline, syncs back on reconnect
- **CMake module** (`src/CMakeBuildNumber.cmake`) — integrates the client into CMake builds via a custom target that runs at build time and generates a C++ version header

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
│ version.h        │
│ build_number.txt │
└──────────────────┘
```

**Data flow:**
1. `cmake --build` triggers the custom target
2. Custom target runs `client.py` via Python
3. Client tries server → falls back to local file → returns build number
4. CMake script writes `version.h` with the number

**Offline sync:** client saves a `.sync` file when working locally. On next successful server contact, it sends its local counter; server takes the max.

## Project Structure

```
build-number-counter/
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
│   └── 3-custom-location/       # Custom counter file path
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

Tests take ~15 seconds total. Integration tests start real server subprocesses on random ports; CMake tests configure and build example projects in temp directories.

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

- **Build time, not configure time.** The build number increments via `add_custom_target(... ALL)`, so it runs on every `cmake --build`, not on `cmake -B`.
- **No external dependencies.** Both client and server use only Python stdlib. pytest is the only dev dependency.
- **Atomic file writes.** Both client and server use temp file + `os.replace()` to avoid partial writes.
- **Server is optional.** Everything works locally without a server; the server adds cross-machine synchronization.
