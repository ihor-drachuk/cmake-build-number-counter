import os
import sys
import json
import threading

import pytest

# Add src/ to path so we can import client and server modules
SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'src')
sys.path.insert(0, os.path.abspath(SRC_DIR))


@pytest.fixture
def tmp_local_file(tmp_path):
    """Return a path to a non-existent file for use as local counter."""
    return str(tmp_path / "build_number.txt")


def _start_server(tmp_path, monkeypatch, *, initial_data=None, accept=True,
                  max_workers=4, handler_timeout=None, max_request_seconds=None):
    """Helper: start in-process server with temp data dir.

    Rate limiting is disabled by default to avoid interference between tests.
    Use the rate_limited_server fixture for rate-limit-specific tests.
    Worker pool is small (4) so 73+ tests do not spawn thousands of threads.

    handler_timeout / max_request_seconds default to None (per-recv timeout
    disabled, wall-clock deadline disabled) so slow CI does not cause
    spurious 408s. Slowloris-specific tests pass explicit small values
    instead of having to monkeypatch around the fixture.
    """
    import server as server_module
    data_dir = str(tmp_path / "server-data")
    server_module.init_data_dir(data_dir)

    # Write initial data
    with open(server_module.BUILD_NUMBERS_FILE, 'w') as f:
        json.dump(initial_data or {}, f)

    monkeypatch.setattr(server_module, 'accept_unknown', accept)
    monkeypatch.setattr(server_module, 'rate_limit', 0)  # disable rate limiting in tests
    monkeypatch.setattr(server_module.BuildNumberHandler, 'timeout', handler_timeout)
    if max_request_seconds is None:
        # Disable wall-clock deadline by setting an effectively-infinite value.
        monkeypatch.setattr(server_module.BuildNumberHandler,
                            'max_request_seconds', 3600)
    else:
        monkeypatch.setattr(server_module.BuildNumberHandler,
                            'max_request_seconds', max_request_seconds)

    httpd = server_module.PooledHTTPServer(
        ('127.0.0.1', 0),
        server_module.BuildNumberHandler,
        max_workers=max_workers,
    )
    port = httpd.server_address[1]

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    return f"http://127.0.0.1:{port}", httpd


def _stop_server(httpd):
    """Shutdown helper: stop accept loop, drain queue, close listening socket."""
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def running_server(tmp_path, monkeypatch):
    """Start an in-process HTTP server that accepts unknown projects."""
    url, httpd = _start_server(tmp_path, monkeypatch, accept=True)
    yield url
    _stop_server(httpd)


@pytest.fixture
def strict_server(tmp_path, monkeypatch):
    """Start server that rejects unknown projects."""
    url, httpd = _start_server(
        tmp_path, monkeypatch,
        initial_data={"approved-project": 0},
        accept=False,
    )
    yield url
    _stop_server(httpd)


@pytest.fixture
def server_small_body_limit(tmp_path, monkeypatch):
    """Server with a 64-byte body size limit."""
    import server as server_module
    url, httpd = _start_server(tmp_path, monkeypatch, accept=True)
    monkeypatch.setattr(server_module, 'max_body_size', 64)
    yield url
    _stop_server(httpd)


@pytest.fixture
def limited_server(tmp_path, monkeypatch):
    """Start server with accept_unknown=True and max_projects=3."""
    import server as server_module
    url, httpd = _start_server(tmp_path, monkeypatch, accept=True)
    monkeypatch.setattr(server_module, 'max_projects', 3)
    yield url
    _stop_server(httpd)


@pytest.fixture
def rate_limited_server(tmp_path, monkeypatch):
    """Server with rate_limit=3 for fast testing."""
    import server as server_module
    url, httpd = _start_server(tmp_path, monkeypatch, accept=True)
    monkeypatch.setattr(server_module, 'rate_limit', 3)
    monkeypatch.setattr(server_module, 'ban_duration', 5)
    monkeypatch.setattr(server_module, 'ban_permanent', False)
    monkeypatch.setattr(server_module, 'rate_tracker', {})
    monkeypatch.setattr(server_module, 'temp_bans', {})
    monkeypatch.setattr(server_module, 'permanent_bans', set())
    yield url
    _stop_server(httpd)


@pytest.fixture
def auth_server(tmp_path, monkeypatch):
    """Start server with token authentication enabled."""
    import server as server_module

    url, httpd = _start_server(tmp_path, monkeypatch, accept=True)

    tokens_data = {
        "tokens": {
            "test-token-project": {
                "name": "project-token",
                "projects": ["test-project"],
                "admin": False,
                "created": "2026-01-01T00:00:00Z"
            },
            "test-token-wildcard": {
                "name": "wildcard-token",
                "projects": ["org-*"],
                "admin": False,
                "created": "2026-01-01T00:00:00Z"
            },
            "test-token-admin": {
                "name": "admin-token",
                "projects": [],
                "admin": True,
                "created": "2026-01-01T00:00:00Z"
            }
        }
    }
    with open(server_module.TOKENS_FILE, 'w') as f:
        json.dump(tokens_data, f)

    yield {
        'url': url,
        'project_token': 'test-token-project',
        'wildcard_token': 'test-token-wildcard',
        'admin_token': 'test-token-admin',
    }
    _stop_server(httpd)


@pytest.fixture
def cmake_auth_server(tmp_path, monkeypatch):
    """Start server with token auth for CMake integration tests."""
    import server as server_module

    url, httpd = _start_server(tmp_path, monkeypatch, accept=True)

    tokens_data = {
        "tokens": {
            "cmake-project-token": {
                "name": "cmake-project",
                "projects": ["cmake-test-project"],
                "admin": False,
                "created": "2026-01-01T00:00:00Z"
            },
            "cmake-wildcard-token": {
                "name": "cmake-wildcard",
                "projects": ["cmake-test-*"],
                "admin": False,
                "created": "2026-01-01T00:00:00Z"
            },
            "cmake-admin-token": {
                "name": "cmake-admin",
                "projects": [],
                "admin": True,
                "created": "2026-01-01T00:00:00Z"
            }
        }
    }
    with open(server_module.TOKENS_FILE, 'w') as f:
        json.dump(tokens_data, f)

    yield {
        'url': url,
        'project_token': 'cmake-project-token',
        'wildcard_token': 'cmake-wildcard-token',
        'admin_token': 'cmake-admin-token',
    }
    _stop_server(httpd)
