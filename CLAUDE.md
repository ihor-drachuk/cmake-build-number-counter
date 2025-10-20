# Build Number Counter

Automatic build number incrementing for CMake projects with optional central server sync.

## Architecture

Three components in `src/`:
- **`server.py`** — HTTP server (`POST /increment`), stores counters in JSON, thread-safe with `file_lock`. Global state: `DATA_DIR`, `BUILD_NUMBERS_FILE`, `accept_unknown` — initialized via `init_data_dir()` in `main()` or by test fixtures.
- **`client.py`** — tries server first, falls back to local file, saves `.sync` state for later reconnection. Entry point: `get_build_number()` returns `(number, was_local)`.
- **`CMakeBuildNumber.cmake`** — provides `increment_build_number()` function. Generates a build-time custom target that calls `client.py` and writes `version.h` with `APP_VERSION_*` defines. Must be called AFTER `project()`.

## Project structure

```
src/                    # Production code (Python + CMake)
tests/                  # pytest tests
  conftest.py           #   Shared fixtures (tmp files, in-process server via init_data_dir)
  test_client.py        #   Unit tests for client.py (no server needed)
  test_server.py        #   Server tests via in-process HTTPServer in a thread
  test_integration.py   #   End-to-end via subprocess (real server + real client)
  test_cmake.py         #   CMake configure+build of examples/1-simple, marked @pytest.mark.cmake
examples/               # 3 self-contained CMake example projects
.github/workflows/      # CI: test.yml (Python matrix + CMake matrix)
```

## Build & test

```bash
# Setup
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements-dev.txt

# Run all tests
python -m pytest tests/ -v

# Skip CMake tests (faster, no compiler needed)
python -m pytest tests/ -v -m "not cmake"

# Run server locally
python src/server.py --accept-unknown

# Run client standalone
python src/client.py --project-key test --quiet
```

## Key conventions

- No external runtime dependencies — only Python stdlib
- Atomic file writes via `os.replace()` (not `os.remove()` + `os.rename()`)
- Server data dir is configurable via `--data-dir` flag; defaults to `server-data/` in project root
- Build number increments at **build time** (custom target), not at CMake configure time
- Tests use `tmp_path` for full isolation — no leftover files in the repo

## Maintaining docs

When changing architecture, adding modules, changing CLI flags, or altering build/test procedures — update the relevant documentation:
- `README.md` — user-facing docs (API reference, quick start, configuration, troubleshooting)
- `docs/CONTRIBUTING.md` — developer onboarding (architecture, structure, setup, how to run)
- `CLAUDE.md` (this file) — keep in sync with the above; if the project structure, conventions, or build commands change, update this file too
