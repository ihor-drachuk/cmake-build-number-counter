# Build Number Counter

Automatic build number incrementing for CMake projects with optional central server sync.

**Before starting any work, read `docs/CONTRIBUTING.md` for architecture, conventions, and testing rules.**

## Architecture

Four components in `src/`:
- **`server.py`** — HTTP server (`POST /increment`, `POST /set`), stores counters in JSON, thread-safe with `file_lock`. Global state: `DATA_DIR`, `BUILD_NUMBERS_FILE`, `TOKENS_FILE`, `accept_unknown`, `max_body_size`, `max_projects`, `rate_limit`, `ban_duration`, `ban_permanent` — initialized via `init_data_dir()` in `main()` or by test fixtures. Rate limiting: per-IP sliding window with `rate_lock`, `rate_tracker`, `temp_bans`, `permanent_bans`; permanent bans persisted to `banned_ips.json`. Optional token auth via `tokens.json` (loaded per-request, supports project-scoped, wildcard, and admin tokens). Token management CLI: `--add-token`, `--remove-token`, `--list-tokens`. Counter management CLI: `--set-counter --project-key X --version N` (offline, no HTTP server).
- **`client.py`** — tries server first, falls back to local file, saves `.sync` state for later reconnection. Entry point: `get_build_number()` returns `(number, was_local)`. Force-set: `force_set_build_number()` sets exact value via `POST /set` or locally. Supports `--server-token` / `BUILD_SERVER_TOKEN` for authenticated requests.
- **`validation.py`** — shared validation (project key format). Imported by both server and client. CMake equivalent is inline in the function.
- **`CMakeBuildNumber.cmake`** — provides `increment_build_number()` function. Two modes: `BUILD` (default, custom target at build time) and `CONFIGURE` (execute_process at configure time, can be called before `project()`). Parameters: `MODE`, `OUTPUT_VARIABLE`, `NO_INCREMENT`, `FORCE_VERSION`, `VERSION_HEADER`, `SERVER_URL`, `SERVER_TOKEN`, `LOCAL_FILE`, `TARGET`, `QUIET`. Configure mode auto-reconfigures on every build via a stamp file: created at configure, deleted by a custom target at build time, triggering reconfigure on next build.

## Project structure

```
src/                    # Production code (Python + CMake)
tests/                  # pytest tests
  conftest.py           #   Shared fixtures (tmp files, in-process server via init_data_dir)
  test_client.py        #   Unit tests for client.py (no server needed)
  test_server.py        #   Server tests via in-process HTTPServer in a thread
  test_integration.py   #   End-to-end via subprocess (real server + real client)
  test_cmake.py         #   CMake tests for BUILD and CONFIGURE modes, marked @pytest.mark.cmake
examples/               # 4 self-contained CMake example projects (1-simple, 2-with-server, 3-custom-location, 4-configure-mode)
docs/                   # Detailed docs (API, server, security, troubleshooting, contributing)
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

# Force-set a counter via server CLI (offline)
python src/server.py --set-counter --project-key test --version 42

# Force-set via client
python src/client.py --project-key test --force-version 42 --quiet
```

## Key conventions

- No external runtime dependencies — only Python stdlib
- Atomic file writes via `os.replace()` (not `os.remove()` + `os.rename()`)
- Server data dir is configurable via `--data-dir` flag; defaults to `server-data/` in project root
- Build number increments on every `cmake --build` in both modes (BUILD via custom target, CONFIGURE via auto-reconfigure)
- Tests use `tmp_path` for full isolation — no leftover files in the repo
- **Increment tests must verify at least 2 consecutive increments via separate cmake invocations** (2 separate `cmake --build` or 2 separate `cmake configure` runs) — a single increment (0→1) is not sufficient to confirm correctness. Do not just call `increment_build_number()` twice in the same CMakeLists.txt — that tests in-process behavior, not real usage.

## Engineering principles

- **Fix root causes, not symptoms.** When a test fails or a bug appears, find and fix the underlying problem. Don't mask failures with workarounds, catch-and-ignore, or cosmetic patches. Reliable solutions > quick hacks.
- **Verify examples and docs hands-on.** Every example project in `examples/` and every CMake snippet in docs must be manually tested (configure + build + run) after creation or editing. Never assume code in documentation works — check it.
- **After every implementation change:**
  1. Write or update tests covering the new/changed behavior
  2. Run the full test suite and ensure all tests pass
  3. Review the changes for correctness and edge cases
  4. Do a security review of the changes and the affected surface
  5. Update documentation (README.md, CONTRIBUTING.md, CLAUDE.md) if the change affects architecture, CLI flags, APIs, or conventions

## Maintaining docs

When changing architecture, adding modules, changing CLI flags, or altering build/test procedures — update the relevant documentation:
- `README.md` — landing page: features, quick start, links to docs/
- `docs/API.md` — CMake function reference, client CLI, configuration priority
- `docs/SERVER.md` — server setup, CLI flags, endpoints, deployment
- `docs/SECURITY.md` — authentication, rate limiting, hardening
- `docs/TROUBLESHOOTING.md` — common issues and solutions
- `docs/CONTRIBUTING.md` — developer onboarding (architecture, structure, setup, how to run)
- `CLAUDE.md` (this file) — keep in sync with the above; if the project structure, conventions, or build commands change, update this file too
