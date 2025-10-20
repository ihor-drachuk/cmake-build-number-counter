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
    """Helper: start in-process server with temp data dir."""
    import server as server_module
    from http.server import HTTPServer

    data_dir = str(tmp_path / "server-data")
    server_module.init_data_dir(data_dir)

    # Write initial data
    with open(server_module.BUILD_NUMBERS_FILE, 'w') as f:
        json.dump(initial_data or {}, f)

    monkeypatch.setattr(server_module, 'accept_unknown', accept)

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
