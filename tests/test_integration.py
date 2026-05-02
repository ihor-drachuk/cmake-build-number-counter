import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'src')
CLIENT_PY = os.path.join(SRC_DIR, 'client.py')
SERVER_PY = os.path.join(SRC_DIR, 'server.py')


def run_client(project_key, local_file, server_url=None, quiet=True, server_token=None,
               expect_failure=False):
    """Run client.py as subprocess.

    Returns stdout stripped on success, or the full CompletedProcess when
    expect_failure=True (caller checks returncode and stderr).
    """
    cmd = [sys.executable, CLIENT_PY, '--project-key', project_key, '--local-file', local_file]
    if server_url:
        cmd += ['--server-url', server_url]
    if server_token:
        cmd += ['--server-token', server_token]
    if quiet:
        cmd += ['--quiet']
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if expect_failure:
        return result
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


def start_server(port, data_dir, tokens=None, rate_limit=None, ban_duration=None):
    """Start server subprocess with --data-dir pointing to temp directory.

    Args:
        tokens: Optional dict to write as tokens.json before starting.
        rate_limit: Optional int for --rate-limit flag.
        ban_duration: Optional int for --ban-duration flag.
    """
    if tokens is not None:
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, "tokens.json"), 'w') as f:
            json.dump(tokens, f)

    cmd = [
        sys.executable, SERVER_PY,
        '--port', str(port),
        '--host', '127.0.0.1',
        '--data-dir', data_dir,
        '--accept-unknown',
    ]
    if rate_limit is not None:
        cmd += ['--rate-limit', str(rate_limit)]
    if ban_duration is not None:
        cmd += ['--ban-duration', str(ban_duration)]

    return subprocess.Popen(
        cmd,
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

    def test_quiet_does_not_suppress_fallback_warning(self, tmp_path):
        """--quiet + unreachable server: warning appears on stderr, fallback succeeds."""
        local_file = str(tmp_path / "counter.txt")
        # Port 1 is reserved/unbound — connection refused without DNS lookup.
        cmd = [sys.executable, CLIENT_PY, '--project-key', 'qtest',
               '--local-file', local_file,
               '--server-url', 'http://127.0.0.1:1',
               '--quiet']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        assert result.stdout.strip() == "1"
        # Warning channel must bypass --quiet: cause + consequence both visible.
        assert "Server unavailable" in result.stderr
        assert "WARNING" in result.stderr
        # Sync follow-up is info — it must stay suppressed by --quiet.
        assert "will sync to server" not in result.stderr


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
        """Client works offline (server configured but unreachable), then syncs."""
        local_file = str(tmp_path / "counter.txt")

        # Start server so we know the port, then stop it to simulate outage
        data_dir = str(tmp_path / "server-data")
        port = pick_free_port()
        server_url = f"http://127.0.0.1:{port}"

        # Work offline with server configured but unreachable: get 1, 2
        assert run_client("sync-test", local_file, server_url=server_url) == "1"
        assert run_client("sync-test", local_file, server_url=server_url) == "2"

        # Start server — client should sync local=2, then increment to 3
        proc = start_server(port, data_dir)
        try:
            wait_for_server(server_url)
            result = run_client("sync-test", local_file, server_url=server_url)
            assert result == "3"
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestOnlineOfflineOnline:
    def test_online_offline_online_continuous(self, tmp_path):
        """Online x2, server dies, offline x2, server restarts, sync and continue."""
        data_dir = str(tmp_path / "server-data")
        local_file = str(tmp_path / "counter.txt")
        port = pick_free_port()
        server_url = f"http://127.0.0.1:{port}"

        # Phase 1: online
        proc = start_server(port, data_dir)
        try:
            wait_for_server(server_url)
            assert run_client("oo-test", local_file, server_url=server_url) == "1"
            assert run_client("oo-test", local_file, server_url=server_url) == "2"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

        # Phase 2: offline fallback
        assert run_client("oo-test", local_file, server_url=server_url) == "3"
        assert run_client("oo-test", local_file, server_url=server_url) == "4"

        # Phase 3: server back — sync and continue
        proc = start_server(port, data_dir)
        try:
            wait_for_server(server_url)
            assert run_client("oo-test", local_file, server_url=server_url) == "5"
            # Confirm server persisted the synced value
            assert run_client("oo-test", local_file, server_url=server_url) == "6"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_online_offline_online_server_advanced(self, tmp_path):
        """Online x2, offline x2, someone else advances server, reconnect takes max."""
        data_dir = str(tmp_path / "server-data")
        local_file = str(tmp_path / "counter.txt")
        port = pick_free_port()
        server_url = f"http://127.0.0.1:{port}"

        # Phase 1: online
        proc = start_server(port, data_dir)
        try:
            wait_for_server(server_url)
            assert run_client("adv-test", local_file, server_url=server_url) == "1"
            assert run_client("adv-test", local_file, server_url=server_url) == "2"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

        # Phase 2: offline fallback
        assert run_client("adv-test", local_file, server_url=server_url) == "3"
        assert run_client("adv-test", local_file, server_url=server_url) == "4"

        # Phase 3: server back, but someone else incremented 10 times
        proc = start_server(port, data_dir)
        try:
            wait_for_server(server_url)

            # Advance server counter from 2 to 12 via /set
            req = urllib.request.Request(
                f"{server_url}/set",
                data=json.dumps({"project_key": "adv-test", "version": 12}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()

            # Client syncs: server has 12, local has 4 → max(12,4)+1 = 13
            assert run_client("adv-test", local_file, server_url=server_url) == "13"
            # Confirm server persisted the synced value
            assert run_client("adv-test", local_file, server_url=server_url) == "14"
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestAuthIntegration:
    def test_roundtrip_with_token(self, tmp_path):
        """Server with auth enabled, client sends valid token."""
        data_dir = str(tmp_path / "server-data")
        port = pick_free_port()
        server_url = f"http://127.0.0.1:{port}"

        tokens_data = {
            "tokens": {
                "integration-token": {
                    "name": "test",
                    "projects": ["auth-test"],
                    "admin": False,
                    "created": "2026-01-01T00:00:00Z"
                }
            }
        }

        proc = start_server(port, data_dir, tokens=tokens_data)
        try:
            wait_for_server(server_url)
            local_file = str(tmp_path / "counter.txt")

            result = run_client("auth-test", local_file,
                                server_url=server_url, server_token="integration-token")
            assert result == "1"

            result = run_client("auth-test", local_file,
                                server_url=server_url, server_token="integration-token")
            assert result == "2"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_rejected_without_token_fails(self, tmp_path):
        """Server with auth enabled, client sends no token → build fails."""
        data_dir = str(tmp_path / "server-data")
        port = pick_free_port()
        server_url = f"http://127.0.0.1:{port}"

        tokens_data = {
            "tokens": {
                "some-token": {
                    "name": "test",
                    "projects": ["proj"],
                    "admin": False,
                    "created": "2026-01-01T00:00:00Z"
                }
            }
        }

        proc = start_server(port, data_dir, tokens=tokens_data)
        try:
            wait_for_server(server_url)
            local_file = str(tmp_path / "counter.txt")

            # No token → 401 → client exits with error
            result = run_client("proj", local_file, server_url=server_url,
                                expect_failure=True)
            assert result.returncode != 0
            assert "Server rejected" in result.stderr
            assert "401" in result.stderr
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestRateLimitIntegration:
    def test_rate_limit_fails_build(self, tmp_path):
        """Client fails when rate-limited by server (no silent fallback)."""
        data_dir = str(tmp_path / "server-data")
        port = pick_free_port()
        server_url = f"http://127.0.0.1:{port}"

        # rate_limit=3: wait_for_server uses 1 slot, leaving 2 for client
        proc = start_server(port, data_dir, rate_limit=3, ban_duration=2)
        try:
            wait_for_server(server_url)
            local_file = str(tmp_path / "counter.txt")

            # First 2 requests succeed on server (2 of 3 slots remaining)
            r1 = run_client("rate-test", local_file, server_url=server_url)
            r2 = run_client("rate-test", local_file, server_url=server_url)
            assert r1 == "1"
            assert r2 == "2"

            # 3rd client request exceeds limit → 429 → client exits with error
            r3 = run_client("rate-test", local_file, server_url=server_url,
                            expect_failure=True)
            assert r3.returncode != 0
            assert "Server rejected" in r3.stderr
            assert "429" in r3.stderr
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestClientForceVersion:
    def test_force_version_local_only(self, tmp_path):
        """--force-version sets the local counter without server."""
        local_file = str(tmp_path / "counter.txt")
        cmd = [sys.executable, CLIENT_PY,
               '--project-key', 'test',
               '--local-file', local_file,
               '--force-version', '42',
               '--quiet']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        assert result.stdout.strip() == "42"

    def test_force_version_with_server(self, tmp_path):
        """--force-version updates both server and local counter."""
        data_dir = str(tmp_path / "server-data")
        port = pick_free_port()
        server_url = f"http://127.0.0.1:{port}"

        proc = start_server(port, data_dir)
        try:
            wait_for_server(server_url)
            local_file = str(tmp_path / "counter.txt")

            # Force-set to 50
            cmd = [sys.executable, CLIENT_PY,
                   '--project-key', 'test',
                   '--local-file', local_file,
                   '--server-url', server_url,
                   '--force-version', '50',
                   '--quiet']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            assert result.returncode == 0
            assert result.stdout.strip() == "50"

            # Next increment should return 51
            assert run_client("test", local_file, server_url=server_url) == "51"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_force_version_to_zero(self, tmp_path):
        """--force-version 0 resets the counter."""
        local_file = str(tmp_path / "counter.txt")
        # First set to 10
        cmd = [sys.executable, CLIENT_PY,
               '--project-key', 'test',
               '--local-file', local_file,
               '--force-version', '10',
               '--quiet']
        subprocess.run(cmd, capture_output=True, text=True, timeout=10)

        # Reset to 0
        cmd = [sys.executable, CLIENT_PY,
               '--project-key', 'test',
               '--local-file', local_file,
               '--force-version', '0',
               '--quiet']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        assert result.stdout.strip() == "0"


class TestServerSetCounter:
    def test_set_counter_creates_project(self, tmp_path):
        """--set-counter creates a new project in build_numbers.json."""
        data_dir = str(tmp_path / "server-data")
        cmd = [sys.executable, SERVER_PY,
               '--set-counter',
               '--project-key', 'new-proj',
               '--version', '42',
               '--data-dir', data_dir]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0

        with open(os.path.join(data_dir, "build_numbers.json")) as f:
            data = json.load(f)
        assert data["new-proj"] == 42

    def test_set_counter_overwrites(self, tmp_path):
        """--set-counter overwrites an existing counter value."""
        data_dir = str(tmp_path / "server-data")
        os.makedirs(data_dir)
        with open(os.path.join(data_dir, "build_numbers.json"), 'w') as f:
            json.dump({"proj": 100}, f)

        cmd = [sys.executable, SERVER_PY,
               '--set-counter',
               '--project-key', 'proj',
               '--version', '5',
               '--data-dir', data_dir]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0

        with open(os.path.join(data_dir, "build_numbers.json")) as f:
            data = json.load(f)
        assert data["proj"] == 5

    def test_set_counter_to_zero(self, tmp_path):
        """--set-counter to 0 is allowed."""
        data_dir = str(tmp_path / "server-data")
        cmd = [sys.executable, SERVER_PY,
               '--set-counter',
               '--project-key', 'proj',
               '--version', '0',
               '--data-dir', data_dir]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0

        with open(os.path.join(data_dir, "build_numbers.json")) as f:
            data = json.load(f)
        assert data["proj"] == 0

    def test_set_counter_missing_project_key(self, tmp_path):
        """--set-counter without --project-key fails."""
        data_dir = str(tmp_path / "server-data")
        cmd = [sys.executable, SERVER_PY,
               '--set-counter',
               '--version', '10',
               '--data-dir', data_dir]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        assert result.returncode != 0

    def test_set_counter_missing_version(self, tmp_path):
        """--set-counter without --version fails."""
        data_dir = str(tmp_path / "server-data")
        cmd = [sys.executable, SERVER_PY,
               '--set-counter',
               '--project-key', 'proj',
               '--data-dir', data_dir]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        assert result.returncode != 0

    def test_set_counter_no_server_started(self, tmp_path):
        """--set-counter exits immediately without starting HTTP server."""
        data_dir = str(tmp_path / "server-data")
        cmd = [sys.executable, SERVER_PY,
               '--set-counter',
               '--project-key', 'proj',
               '--version', '10',
               '--data-dir', data_dir]
        # Should complete quickly (no server loop)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        assert result.returncode == 0
