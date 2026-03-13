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


def _start_server(tmp_path, monkeypatch, *, initial_data=None, accept=True):
    """Helper: start in-process server with temp data dir.

    Rate limiting is disabled by default to avoid interference between tests.
    Use the rate_limited_server fixture for rate-limit-specific tests.
    """
    import server as server_module
    from http.server import HTTPServer

    data_dir = str(tmp_path / "server-data")
    server_module.init_data_dir(data_dir)

    # Write initial data
    with open(server_module.BUILD_NUMBERS_FILE, 'w') as f:
        json.dump(initial_data or {}, f)

    monkeypatch.setattr(server_module, 'accept_unknown', accept)
    monkeypatch.setattr(server_module, 'rate_limit', 0)  # disable rate limiting in tests

    httpd = HTTPServer(('127.0.0.1', 0), server_module.BuildNumberHandler)
    port = httpd.server_address[1]

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    return f"http://127.0.0.1:{port}", httpd


@pytest.fixture
def running_server(tmp_path, monkeypatch):
    """Start an in-process HTTP server that accepts unknown projects."""
    url, httpd = _start_server(tmp_path, monkeypatch, accept=True)
    yield url
    httpd.shutdown()


@pytest.fixture
def strict_server(tmp_path, monkeypatch):
    """Start server that rejects unknown projects."""
    url, httpd = _start_server(
        tmp_path, monkeypatch,
        initial_data={"approved-project": 0},
        accept=False,
    )
    yield url
    httpd.shutdown()


@pytest.fixture
def server_small_body_limit(tmp_path, monkeypatch):
    """Server with a 64-byte body size limit."""
    import server as server_module
    url, httpd = _start_server(tmp_path, monkeypatch, accept=True)
    monkeypatch.setattr(server_module, 'max_body_size', 64)
    yield url
    httpd.shutdown()


@pytest.fixture
def limited_server(tmp_path, monkeypatch):
    """Start server with accept_unknown=True and max_projects=3."""
    import server as server_module
    url, httpd = _start_server(tmp_path, monkeypatch, accept=True)
    monkeypatch.setattr(server_module, 'max_projects', 3)
    yield url
    httpd.shutdown()


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
    httpd.shutdown()


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
    httpd.shutdown()
