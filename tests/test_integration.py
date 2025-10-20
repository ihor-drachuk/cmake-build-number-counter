import json
import os
import socket
import subprocess
import sys
import time

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'src')
CLIENT_PY = os.path.join(SRC_DIR, 'client.py')
SERVER_PY = os.path.join(SRC_DIR, 'server.py')


def run_client(project_key, local_file, server_url=None, quiet=True):
    """Run client.py as subprocess, return stdout stripped."""
    cmd = [sys.executable, CLIENT_PY, '--project-key', project_key, '--local-file', local_file]
    if server_url:
        cmd += ['--server-url', server_url]
    if quiet:
        cmd += ['--quiet']
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, f"client.py failed: {result.stderr}"
    return result.stdout.strip()


def wait_for_server(url, timeout=5):
    """Wait for server to respond to GET /."""
    import urllib.request
    import urllib.error
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/", timeout=1):
                return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.1)
    raise RuntimeError(f"Server at {url} did not start within {timeout}s")


def start_server(port, data_dir):
    """Start server subprocess with --data-dir pointing to temp directory."""
    return subprocess.Popen(
        [sys.executable, SERVER_PY,
         '--port', str(port),
         '--host', '127.0.0.1',
         '--data-dir', data_dir,
         '--accept-unknown'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def pick_free_port():
    """Find a free port on localhost."""
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class TestClientLocalFallback:
    def test_increments_without_server(self, tmp_path):
        local_file = str(tmp_path / "counter.txt")
        assert run_client("test-proj", local_file) == "1"
        assert run_client("test-proj", local_file) == "2"
        assert run_client("test-proj", local_file) == "3"

    def test_output_format_json(self, tmp_path):
        local_file = str(tmp_path / "counter.txt")
        cmd = [sys.executable, CLIENT_PY, '--project-key', 'test', '--local-file', local_file, '--output-format', 'json', '--quiet']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data['build_number'] == 1
        assert data['project_key'] == 'test'


class TestClientServerIntegration:
    def test_client_server_roundtrip(self, tmp_path):
        """Start server, run client 3 times, verify incrementing."""
        data_dir = str(tmp_path / "server-data")
        port = pick_free_port()
        server_url = f"http://127.0.0.1:{port}"

        proc = start_server(port, data_dir)
        try:
            wait_for_server(server_url)
            local_file = str(tmp_path / "counter.txt")

            assert run_client("integration-test", local_file, server_url=server_url) == "1"
            assert run_client("integration-test", local_file, server_url=server_url) == "2"
            assert run_client("integration-test", local_file, server_url=server_url) == "3"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_sync_after_offline(self, tmp_path):
        """Client works offline, then syncs when server becomes available."""
        local_file = str(tmp_path / "counter.txt")

        # Work offline: get 1, 2
        assert run_client("sync-test", local_file) == "1"
        assert run_client("sync-test", local_file) == "2"

        # Start server
        data_dir = str(tmp_path / "server-data")
        port = pick_free_port()
        server_url = f"http://127.0.0.1:{port}"

        proc = start_server(port, data_dir)
        try:
            wait_for_server(server_url)
            # Next call should sync local=2 to server, then increment to 3
            result = run_client("sync-test", local_file, server_url=server_url)
            assert result == "3"
        finally:
            proc.terminate()
            proc.wait(timeout=5)
