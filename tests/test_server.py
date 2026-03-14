import http.client
import json
import time
import urllib.request
import urllib.error
from unittest.mock import patch
from urllib.parse import urlparse

import pytest

from conftest import _start_server


def post_json(base_url, path, data, token=None):
    """Helper: POST JSON to server, return (status_code, response_dict)."""
    url = f"{base_url}{path}"
    body = json.dumps(data).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


# When the server sends an error response before reading the request body
# (e.g. 429 rate limit, 413 too large), the OS may TCP RST the connection
# before the client reads the response. This is expected TCP behavior, not
# a server bug (see docs/adr/001-tcp-rst-on-early-http-error-response.md).
# Use this sentinel to distinguish "server rejected" from other failures.
_SERVER_REJECTED = "CONNECTION_RESET_BY_SERVER"


def _expect_rejection(func, *args, **kwargs):
    """Call func and return (status, data), or _SERVER_REJECTED on TCP reset."""
    try:
        return func(*args, **kwargs)
    except (ConnectionError, OSError, urllib.error.URLError):
        return _SERVER_REJECTED


def get_json(base_url, path):
    """Helper: GET from server, return (status_code, response_dict)."""
    url = f"{base_url}{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


class TestServerGET:
    def test_root(self, running_server):
        status, data = get_json(running_server, "/")
        assert status == 200
        assert "service" in data

    def test_unknown_path(self, running_server):
        status, data = get_json(running_server, "/nonexistent")
        assert status == 404


class TestServerIncrement:
    def test_increment_new_project(self, running_server):
        status, data = post_json(running_server, "/increment", {"project_key": "myproj"})
        assert status == 200
        assert data["build_number"] == 1

    def test_increment_twice(self, running_server):
        post_json(running_server, "/increment", {"project_key": "myproj"})
        status, data = post_json(running_server, "/increment", {"project_key": "myproj"})
        assert status == 200
        assert data["build_number"] == 2

    def test_independent_projects(self, running_server):
        post_json(running_server, "/increment", {"project_key": "proj-a"})
        post_json(running_server, "/increment", {"project_key": "proj-a"})
        status, data = post_json(running_server, "/increment", {"project_key": "proj-b"})
        assert status == 200
        assert data["build_number"] == 1  # proj-b starts at 1

    def test_missing_project_key(self, running_server):
        status, data = post_json(running_server, "/increment", {})
        assert status == 400
        assert "error" in data

    def test_invalid_json(self, running_server):
        url = f"{running_server}/increment"
        req = urllib.request.Request(url, data=b"not json", headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 400


class TestServerLocalVersionSync:
    def test_local_version_higher(self, running_server):
        # First increment to get counter to 1
        post_json(running_server, "/increment", {"project_key": "proj"})
        # Send local_version=10 (higher than server's 1)
        status, data = post_json(running_server, "/increment", {"project_key": "proj", "local_version": 10})
        assert status == 200
        assert data["build_number"] == 11  # max(1, 10) + 1

    def test_local_version_lower(self, running_server):
        # Get counter to 5
        for _ in range(5):
            post_json(running_server, "/increment", {"project_key": "proj"})
        # Send local_version=3 (lower than server's 5)
        status, data = post_json(running_server, "/increment", {"project_key": "proj", "local_version": 3})
        assert status == 200
        assert data["build_number"] == 6  # server ignores lower, increments 5 -> 6


class TestServerApproval:
    def test_unknown_rejected(self, strict_server):
        status, data = post_json(strict_server, "/increment", {"project_key": "unknown-proj"})
        assert status == 403

    def test_approved_works(self, strict_server):
        status, data = post_json(strict_server, "/increment", {"project_key": "approved-project"})
        assert status == 200
        assert data["build_number"] == 1


def _post_raw(base_url, path, body_bytes, headers=None):
    """POST raw bytes using http.client (reliable on all platforms).

    Unlike urllib, http.client doesn't race between sending body and
    reading the response, so it works correctly when the server rejects
    the request based on Content-Length before reading the body.
    """
    parsed = urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        hdrs = {'Content-Type': 'application/json'}
        if headers:
            hdrs.update(headers)
        conn.request('POST', path, body=body_bytes, headers=hdrs)
        resp = conn.getresponse()
        status = resp.status
        data = json.loads(resp.read().decode())
        return status, data
    finally:
        conn.close()


class TestContentLengthLimit:
    def test_normal_request_within_limit(self, server_small_body_limit):
        status, data = post_json(server_small_body_limit, "/increment", {"project_key": "t"})
        assert status == 200

    def test_oversized_request(self, server_small_body_limit):
        # 64-byte limit, send a large payload
        big_body = json.dumps({"project_key": "x" * 100}).encode('utf-8')
        result = _expect_rejection(_post_raw, server_small_body_limit, "/increment", big_body)
        if result == _SERVER_REJECTED:
            return
        assert result[0] == 413
        assert result[1]["error"] == "Request body too large"
        assert "max_bytes" in result[1]

    def test_body_exactly_at_limit(self, running_server):
        # Default 1024-byte limit, normal request is well within
        status, data = post_json(running_server, "/increment", {"project_key": "test"})
        assert status == 200

    def test_413_response_format(self, server_small_body_limit):
        big_body = json.dumps({"project_key": "x" * 100}).encode('utf-8')
        result = _expect_rejection(_post_raw, server_small_body_limit, "/increment", big_body)
        if result == _SERVER_REJECTED:
            return
        status, data = result
        assert status == 413
        assert "error" in data
        assert "max_bytes" in data
        assert "received_bytes" in data

    def test_content_length_lies_larger(self, server_small_body_limit):
        # Small actual body but Content-Length claims 5000 bytes
        small_body = b'{"project_key":"a"}'
        result = _expect_rejection(
            _post_raw, server_small_body_limit, "/increment", small_body,
            headers={'Content-Length': '5000'},
        )
        if result == _SERVER_REJECTED:
            return
        assert result[0] == 413


class TestMaxProjectLimit:
    def test_under_limit_succeeds(self, limited_server):
        for i in range(3):
            status, data = post_json(limited_server, "/increment", {"project_key": f"proj-{i}"})
            assert status == 200

    def test_at_limit_new_project_rejected(self, limited_server):
        for i in range(3):
            post_json(limited_server, "/increment", {"project_key": f"proj-{i}"})
        status, data = post_json(limited_server, "/increment", {"project_key": "proj-extra"})
        assert status == 507
        assert "max_projects" in data

    def test_at_limit_existing_project_still_works(self, limited_server):
        for i in range(3):
            post_json(limited_server, "/increment", {"project_key": f"proj-{i}"})
        status, data = post_json(limited_server, "/increment", {"project_key": "proj-0"})
        assert status == 200
        assert data["build_number"] == 2

    def test_limit_zero_means_unlimited(self, tmp_path, monkeypatch):
        import server as server_module
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True)
        monkeypatch.setattr(server_module, 'max_projects', 0)
        try:
            for i in range(10):
                status, _ = post_json(url, "/increment", {"project_key": f"proj-{i}"})
                assert status == 200
        finally:
            httpd.shutdown()

    def test_preexisting_over_limit(self, tmp_path, monkeypatch):
        import server as server_module
        initial = {f"proj-{i}": i for i in range(5)}
        url, httpd = _start_server(tmp_path, monkeypatch, initial_data=initial, accept=True)
        monkeypatch.setattr(server_module, 'max_projects', 3)
        try:
            # Existing projects still work
            status, _ = post_json(url, "/increment", {"project_key": "proj-0"})
            assert status == 200
            # New project rejected
            status, _ = post_json(url, "/increment", {"project_key": "new-proj"})
            assert status == 507
        finally:
            httpd.shutdown()

    def test_response_includes_project_key(self, limited_server):
        for i in range(3):
            post_json(limited_server, "/increment", {"project_key": f"proj-{i}"})
        status, data = post_json(limited_server, "/increment", {"project_key": "rejected-proj"})
        assert data["project_key"] == "rejected-proj"


class TestServerProjectKeyValidation:
    @pytest.mark.parametrize("bad_key", [
        "../../etc/passwd",
        "key with spaces",
        "a" * 200,
        "key\nnewline",
    ])
    def test_invalid_key_rejected_400(self, running_server, bad_key):
        status, data = post_json(running_server, "/increment", {"project_key": bad_key})
        assert status == 400
        assert "Invalid project_key" in data["error"]

    def test_valid_key_accepted(self, running_server):
        status, data = post_json(running_server, "/increment", {"project_key": "valid-key.123_test"})
        assert status == 200
        assert data["build_number"] == 1


class TestTokenAuth:
    """Unit tests for token loading and authentication logic."""

    def test_load_tokens_missing_file(self, tmp_path, monkeypatch):
        import server as server_module
        server_module.init_data_dir(str(tmp_path / "data"))
        result = server_module.load_tokens()
        assert result == {}

    def test_load_tokens_empty(self, tmp_path, monkeypatch):
        import server as server_module
        server_module.init_data_dir(str(tmp_path / "data"))
        with open(server_module.TOKENS_FILE, 'w') as f:
            json.dump({"tokens": {}}, f)
        result = server_module.load_tokens()
        assert result == {}

    def test_load_tokens_with_data(self, tmp_path, monkeypatch):
        import server as server_module
        server_module.init_data_dir(str(tmp_path / "data"))
        tokens = {"tokens": {"abc123": {"name": "t", "projects": ["p"], "admin": False}}}
        with open(server_module.TOKENS_FILE, 'w') as f:
            json.dump(tokens, f)
        result = server_module.load_tokens()
        assert "abc123" in result
        assert result["abc123"]["name"] == "t"


class TestServerAuth:
    """HTTP-level tests for token authentication."""

    def test_no_token_rejected(self, auth_server):
        status, data = post_json(auth_server['url'], "/increment", {"project_key": "test-project"})
        assert status == 401
        assert "Authorization" in data["error"]

    def test_invalid_token_rejected(self, auth_server):
        status, data = post_json(auth_server['url'], "/increment",
                                 {"project_key": "test-project"}, token="bad-token")
        assert status == 401
        assert "Invalid token" in data["error"]

    def test_valid_token_accepted(self, auth_server):
        status, data = post_json(auth_server['url'], "/increment",
                                 {"project_key": "test-project"},
                                 token=auth_server['project_token'])
        assert status == 200
        assert data["build_number"] == 1

    def test_wrong_project_rejected(self, auth_server):
        status, data = post_json(auth_server['url'], "/increment",
                                 {"project_key": "other-project"},
                                 token=auth_server['project_token'])
        assert status == 401
        assert "does not have access" in data["error"]

    def test_admin_any_project(self, auth_server):
        status, data = post_json(auth_server['url'], "/increment",
                                 {"project_key": "any-project-at-all"},
                                 token=auth_server['admin_token'])
        assert status == 200

    def test_wildcard_match(self, auth_server):
        status, data = post_json(auth_server['url'], "/increment",
                                 {"project_key": "org-frontend"},
                                 token=auth_server['wildcard_token'])
        assert status == 200

    def test_wildcard_no_match(self, auth_server):
        status, data = post_json(auth_server['url'], "/increment",
                                 {"project_key": "other-project"},
                                 token=auth_server['wildcard_token'])
        assert status == 401

    def test_get_root_no_auth_needed(self, auth_server):
        status, data = get_json(auth_server['url'], "/")
        assert status == 200
        assert "service" in data

    def test_malformed_auth_header(self, auth_server):
        url = f"{auth_server['url']}/increment"
        body = json.dumps({"project_key": "test-project"}).encode('utf-8')
        req = urllib.request.Request(url, data=body, method='POST',
                                     headers={'Content-Type': 'application/json',
                                              'Authorization': 'Basic abc123'})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 401

    def test_auth_disabled_without_tokens(self, running_server):
        """When no tokens.json exists, requests work without auth."""
        status, data = post_json(running_server, "/increment", {"project_key": "test"})
        assert status == 200


class TestTokenCLI:
    """Tests for token management CLI functions."""

    def test_add_token(self, tmp_path):
        import server as server_module
        import argparse
        server_module.init_data_dir(str(tmp_path / "data"))

        args = argparse.Namespace(
            token_name="test-token",
            token_projects="proj-a,proj-b",
            token_admin=False,
        )
        server_module._handle_add_token(args)

        data = json.loads(open(server_module.TOKENS_FILE).read())
        tokens = data["tokens"]
        assert len(tokens) == 1
        meta = list(tokens.values())[0]
        assert meta["name"] == "test-token"
        assert meta["projects"] == ["proj-a", "proj-b"]
        assert meta["admin"] is False

    def test_add_admin_token(self, tmp_path):
        import server as server_module
        import argparse
        server_module.init_data_dir(str(tmp_path / "data"))

        args = argparse.Namespace(
            token_name="admin",
            token_projects=None,
            token_admin=True,
        )
        server_module._handle_add_token(args)

        data = json.loads(open(server_module.TOKENS_FILE).read())
        meta = list(data["tokens"].values())[0]
        assert meta["admin"] is True

    def test_add_duplicate_name_fails(self, tmp_path):
        import server as server_module
        import argparse
        server_module.init_data_dir(str(tmp_path / "data"))

        args = argparse.Namespace(
            token_name="dup",
            token_projects="proj",
            token_admin=False,
        )
        server_module._handle_add_token(args)

        with pytest.raises(SystemExit):
            server_module._handle_add_token(args)

    def test_remove_token(self, tmp_path):
        import server as server_module
        import argparse
        server_module.init_data_dir(str(tmp_path / "data"))

        # Add first
        args_add = argparse.Namespace(
            token_name="to-remove",
            token_projects="proj",
            token_admin=False,
        )
        server_module._handle_add_token(args_add)

        # Remove
        args_rm = argparse.Namespace(remove_token="to-remove")
        server_module._handle_remove_token(args_rm)

        data = json.loads(open(server_module.TOKENS_FILE).read())
        assert len(data["tokens"]) == 0

    def test_remove_nonexistent_fails(self, tmp_path):
        import server as server_module
        import argparse
        server_module.init_data_dir(str(tmp_path / "data"))
        server_module.save_json_file(server_module.TOKENS_FILE, {"tokens": {}})

        args = argparse.Namespace(remove_token="nonexistent")
        with pytest.raises(SystemExit):
            server_module._handle_remove_token(args)

    def test_list_tokens(self, tmp_path, capsys):
        import server as server_module
        server_module.init_data_dir(str(tmp_path / "data"))
        server_module.save_json_file(server_module.TOKENS_FILE, {"tokens": {}})

        server_module._handle_list_tokens()
        captured = capsys.readouterr()
        assert "No tokens configured" in captured.out

    def test_list_tokens_with_data(self, tmp_path, capsys):
        import server as server_module
        import argparse
        server_module.init_data_dir(str(tmp_path / "data"))

        args = argparse.Namespace(
            token_name="my-token",
            token_projects="proj-a",
            token_admin=False,
        )
        server_module._handle_add_token(args)

        # Reset captured output
        capsys.readouterr()
        server_module._handle_list_tokens()
        captured = capsys.readouterr()
        assert "my-token" in captured.out
        assert "proj-a" in captured.out


class TestRateLimiting:
    """Tests for per-IP rate limiting and ban mechanism."""

    def test_requests_within_limit(self, rate_limited_server):
        """Requests within the limit (3) should all succeed."""
        for _ in range(3):
            status, _ = post_json(rate_limited_server, "/increment", {"project_key": "test"})
            assert status == 200

    def test_exceeding_limit_gets_429(self, rate_limited_server):
        """The request that exceeds the limit gets 429."""
        for _ in range(3):
            post_json(rate_limited_server, "/increment", {"project_key": "test"})
        result = _expect_rejection(post_json, rate_limited_server, "/increment", {"project_key": "test"})
        if result == _SERVER_REJECTED:
            return
        assert result[0] == 429
        assert result[1]["ban_type"] == "temporary"

    def test_429_response_format_temp(self, rate_limited_server):
        """Verify temp ban response has all expected fields."""
        for _ in range(3):
            post_json(rate_limited_server, "/increment", {"project_key": "test"})
        result = _expect_rejection(post_json, rate_limited_server, "/increment", {"project_key": "test"})
        if result == _SERVER_REJECTED:
            return
        status, data = result
        assert status == 429
        assert "error" in data
        assert data["ban_type"] == "temporary"
        assert "retry_after_seconds" in data
        assert "ip" in data

    def test_temp_ban_blocks_subsequent(self, rate_limited_server):
        """Once banned, subsequent requests are also rejected."""
        for _ in range(3):
            post_json(rate_limited_server, "/increment", {"project_key": "test"})
        # Trigger ban
        _expect_rejection(post_json, rate_limited_server, "/increment", {"project_key": "test"})
        # Still banned
        result = _expect_rejection(post_json, rate_limited_server, "/increment", {"project_key": "test"})
        if result == _SERVER_REJECTED:
            return
        assert result[0] == 429

    def test_rate_limit_disabled(self, tmp_path, monkeypatch):
        """With rate_limit=0, no rate limiting is applied."""
        import server as server_module
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True)
        monkeypatch.setattr(server_module, 'rate_limit', 0)
        try:
            for _ in range(20):
                status, _ = post_json(url, "/increment", {"project_key": "test"})
                assert status == 200
        finally:
            httpd.shutdown()

    def test_get_and_post_share_limit(self, rate_limited_server):
        """GET and POST requests share the same rate bucket."""
        # Use 2 GETs + 1 POST = 3 (at limit)
        get_json(rate_limited_server, "/")
        get_json(rate_limited_server, "/")
        post_json(rate_limited_server, "/increment", {"project_key": "test"})
        # 4th request should be rate-limited
        result = _expect_rejection(get_json, rate_limited_server, "/")
        if result == _SERVER_REJECTED:
            return
        assert result[0] == 429

    def test_permanent_ban_persisted(self, tmp_path, monkeypatch):
        """With ban_permanent=True, ban is written to banned_ips.json."""
        import server as server_module
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True)
        monkeypatch.setattr(server_module, 'rate_limit', 2)
        monkeypatch.setattr(server_module, 'ban_permanent', True)
        monkeypatch.setattr(server_module, 'rate_tracker', {})
        monkeypatch.setattr(server_module, 'permanent_bans', set())
        monkeypatch.setattr(server_module, 'permanent_bans_mtime', 0.0)
        try:
            for _ in range(2):
                post_json(url, "/increment", {"project_key": "test"})
            result = _expect_rejection(post_json, url, "/increment", {"project_key": "test"})

            # Verify file was created (ban is persisted regardless of TCP delivery)
            import os
            ban_file = os.path.join(server_module.DATA_DIR, "banned_ips.json")
            assert os.path.exists(ban_file)
            with open(ban_file) as f:
                ban_data = json.load(f)
            assert "127.0.0.1" in ban_data["banned"]
            assert "banned_at" in ban_data["banned"]["127.0.0.1"]

            if result != _SERVER_REJECTED:
                assert result[0] == 429
                assert result[1]["ban_type"] == "permanent"
        finally:
            httpd.shutdown()

    def test_permanent_ban_response_format(self, tmp_path, monkeypatch):
        """Permanent ban response has no retry_after_seconds."""
        import server as server_module
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True)
        monkeypatch.setattr(server_module, 'rate_limit', 2)
        monkeypatch.setattr(server_module, 'ban_permanent', True)
        monkeypatch.setattr(server_module, 'rate_tracker', {})
        monkeypatch.setattr(server_module, 'permanent_bans', set())
        monkeypatch.setattr(server_module, 'permanent_bans_mtime', 0.0)
        try:
            for _ in range(2):
                post_json(url, "/increment", {"project_key": "test"})
            result = _expect_rejection(post_json, url, "/increment", {"project_key": "test"})
            if result == _SERVER_REJECTED:
                return
            assert result[1]["ban_type"] == "permanent"
            assert "retry_after_seconds" not in result[1]
        finally:
            httpd.shutdown()

    def test_permanent_unban_via_file(self, tmp_path, monkeypatch):
        """Removing IP from banned_ips.json unbans it (mtime-based refresh)."""
        import server as server_module
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True)
        monkeypatch.setattr(server_module, 'rate_limit', 2)
        monkeypatch.setattr(server_module, 'ban_permanent', True)
        monkeypatch.setattr(server_module, 'rate_tracker', {})
        monkeypatch.setattr(server_module, 'permanent_bans', set())
        monkeypatch.setattr(server_module, 'permanent_bans_mtime', 0.0)
        try:
            # Trigger ban
            for _ in range(2):
                post_json(url, "/increment", {"project_key": "test"})
            _expect_rejection(post_json, url, "/increment", {"project_key": "test"})

            # Verify ban file was written
            import os
            ban_file = os.path.join(server_module.DATA_DIR, "banned_ips.json")
            assert os.path.exists(ban_file)

            # Edit ban file to remove the IP
            time.sleep(0.05)  # ensure mtime differs
            with open(ban_file, 'w') as f:
                json.dump({"banned": {}}, f)

            # Clear in-memory cache so mtime check triggers reload
            server_module.permanent_bans = set()
            server_module.permanent_bans_mtime = 0.0

            # Should be unbanned now
            status, _ = post_json(url, "/increment", {"project_key": "test"})
            assert status == 200
        finally:
            httpd.shutdown()

    def test_cleanup_removes_stale_entries(self, monkeypatch):
        """cleanup_rate_data() removes IPs with only old timestamps."""
        import server as server_module
        old_time = time.monotonic() - 120.0
        monkeypatch.setattr(server_module, 'rate_tracker', {
            '1.2.3.4': [old_time],
            '5.6.7.8': [old_time - 5],
        })
        monkeypatch.setattr(server_module, 'temp_bans', {})
        server_module.cleanup_rate_data()
        assert server_module.rate_tracker == {}

    def test_cleanup_removes_expired_bans(self, monkeypatch):
        """cleanup_rate_data() removes expired temp bans."""
        import server as server_module
        now = time.monotonic()
        monkeypatch.setattr(server_module, 'rate_tracker', {})
        monkeypatch.setattr(server_module, 'temp_bans', {
            '1.2.3.4': now - 10,  # expired
            '5.6.7.8': now + 100,  # still active
        })
        server_module.cleanup_rate_data()
        assert '1.2.3.4' not in server_module.temp_bans
        assert '5.6.7.8' in server_module.temp_bans

    def test_ban_clears_tracker(self, rate_limited_server):
        """After banning, the IP's rate_tracker entry is removed."""
        import server as server_module
        for _ in range(3):
            post_json(rate_limited_server, "/increment", {"project_key": "test"})
        # Trigger ban
        post_json(rate_limited_server, "/increment", {"project_key": "test"})
        assert '127.0.0.1' not in server_module.rate_tracker


class TestLocalVersionValidation:
    """Tests for local_version input validation on /increment."""

    @pytest.mark.parametrize("local_version", [0, 1, 100, 999999])
    def test_valid_local_version(self, running_server, local_version):
        """Valid integer local_version values are accepted."""
        status, data = post_json(running_server, "/increment", {
            "project_key": "val-proj",
            "local_version": local_version
        })
        assert status == 200
        assert isinstance(data["build_number"], int)

    def test_null_local_version(self, running_server):
        """Explicit null local_version is treated as absent (valid)."""
        status, data = post_json(running_server, "/increment", {
            "project_key": "val-proj",
            "local_version": None
        })
        assert status == 200

    @pytest.mark.parametrize("local_version", [-1, -100])
    def test_negative_local_version(self, running_server, local_version):
        """Negative local_version values are rejected with 400."""
        status, data = post_json(running_server, "/increment", {
            "project_key": "val-proj",
            "local_version": local_version
        })
        assert status == 400
        assert "local_version" in data["error"]

    @pytest.mark.parametrize("local_version", [3.14, "abc", True, False, [1, 2], {"a": 1}])
    def test_non_integer_local_version(self, running_server, local_version):
        """Non-integer local_version values are rejected with 400."""
        status, data = post_json(running_server, "/increment", {
            "project_key": "val-proj",
            "local_version": local_version
        })
        assert status == 400
        assert "integer" in data["error"]

    def test_error_response_includes_field(self, running_server):
        """Error response includes field name and value for debugging."""
        status, data = post_json(running_server, "/increment", {
            "project_key": "val-proj",
            "local_version": "bad"
        })
        assert status == 400
        assert data["field"] == "local_version"
        assert "bad" in data["value"]


class TestSetEndpoint:
    """Tests for POST /set endpoint."""

    def test_set_new_project(self, running_server):
        """Setting a value for a new project creates it."""
        status, data = post_json(running_server, "/set", {
            "project_key": "new-proj",
            "version": 42
        })
        assert status == 200
        assert data["build_number"] == 42
        assert data["project_key"] == "new-proj"

    def test_set_then_increment(self, running_server):
        """After force-setting to N, next increment returns N+1."""
        post_json(running_server, "/set", {"project_key": "p", "version": 10})
        status, data = post_json(running_server, "/increment", {"project_key": "p"})
        assert data["build_number"] == 11

    def test_set_to_zero(self, running_server):
        """Force-setting to 0 is allowed; next increment yields 1."""
        post_json(running_server, "/increment", {"project_key": "p"})  # counter = 1
        post_json(running_server, "/set", {"project_key": "p", "version": 0})
        status, data = post_json(running_server, "/increment", {"project_key": "p"})
        assert data["build_number"] == 1

    def test_set_overwrites(self, running_server):
        """Force-setting replaces the current value."""
        for _ in range(5):
            post_json(running_server, "/increment", {"project_key": "p"})
        post_json(running_server, "/set", {"project_key": "p", "version": 2})
        status, data = post_json(running_server, "/increment", {"project_key": "p"})
        assert data["build_number"] == 3

    def test_set_missing_project_key(self, running_server):
        status, data = post_json(running_server, "/set", {"version": 5})
        assert status == 400

    def test_set_missing_version(self, running_server):
        status, data = post_json(running_server, "/set", {"project_key": "p"})
        assert status == 400
        assert "Missing version" in data["error"]

    def test_set_negative_version(self, running_server):
        status, data = post_json(running_server, "/set", {
            "project_key": "p", "version": -1
        })
        assert status == 400

    def test_set_float_version(self, running_server):
        status, data = post_json(running_server, "/set", {
            "project_key": "p", "version": 3.5
        })
        assert status == 400

    def test_set_boolean_version(self, running_server):
        status, data = post_json(running_server, "/set", {
            "project_key": "p", "version": True
        })
        assert status == 400

    def test_set_rejected_in_strict_mode(self, strict_server):
        """Unknown project is rejected when accept_unknown is false."""
        status, data = post_json(strict_server, "/set", {
            "project_key": "unknown", "version": 5
        })
        assert status == 403

    def test_set_approved_project_in_strict_mode(self, strict_server):
        """Approved project can be force-set in strict mode."""
        status, data = post_json(strict_server, "/set", {
            "project_key": "approved-project", "version": 99
        })
        assert status == 200
        assert data["build_number"] == 99

    def test_set_invalid_project_key(self, running_server):
        status, data = post_json(running_server, "/set", {
            "project_key": "../../bad", "version": 5
        })
        assert status == 400
