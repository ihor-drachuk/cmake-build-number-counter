import http.client
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import urllib.error
from unittest.mock import patch
from urllib.parse import urlparse

import pytest

from conftest import _start_server, _stop_server


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
    """Call func and return (status, data), or _SERVER_REJECTED on TCP reset.

    IncompleteRead also counts as rejection: if the server's early error
    response races with a client-side close, http.client may surface the
    truncated read as IncompleteRead instead of a clean status.
    """
    try:
        return func(*args, **kwargs)
    except (ConnectionError, OSError, urllib.error.URLError,
            http.client.IncompleteRead):
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


def _open_slow_socket(url, content_length=100):
    """Open a TCP connection that sends headers and stalls on the body.

    Used to occupy worker pool slots without ever completing a request.
    Caller is responsible for closing the socket.
    """
    parsed = urlparse(url)
    s = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
    headers = (
        f"POST /increment HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{parsed.port}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {content_length}\r\n"
        f"\r\n"
    )
    s.sendall(headers.encode('ascii'))
    return s


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
            _stop_server(httpd)

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
            _stop_server(httpd)

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


class TestTokensCache:
    """mtime-based cache of tokens.json: skip disk I/O when file unchanged."""

    def _write_tokens(self, server_module, payload):
        """Atomic-write tokens.json so mtime updates reliably."""
        tmp = server_module.TOKENS_FILE + ".tmp"
        with open(tmp, 'w') as f:
            json.dump(payload, f)
        os.replace(tmp, server_module.TOKENS_FILE)

    # --- happy path ---

    def test_cache_loads_on_first_call(self, tmp_path):
        import server as server_module
        server_module.init_data_dir(str(tmp_path / "data"))
        self._write_tokens(server_module, {"tokens": {"k": {"name": "t"}}})
        result = server_module.load_tokens()
        assert "k" in result
        assert server_module._tokens_cache_mtime > 0

    def test_cache_skips_disk_io_on_unchanged_mtime(self, tmp_path, monkeypatch):
        """Repeat calls do not re-read tokens.json while mtime is unchanged."""
        import server as server_module
        server_module.init_data_dir(str(tmp_path / "data"))
        self._write_tokens(server_module, {"tokens": {"k": {"name": "t"}}})

        calls = []
        original = server_module.load_json_file

        def counting(filename, default):
            if filename == server_module.TOKENS_FILE:
                calls.append(filename)
            return original(filename, default)
        monkeypatch.setattr(server_module, 'load_json_file', counting)

        first = server_module.load_tokens()
        assert "k" in first
        for _ in range(10):
            again = server_module.load_tokens()
            assert again == first  # same content
        assert len(calls) == 1, f"expected 1 disk read, got {len(calls)}"

    def test_cache_returns_defensive_copy(self, tmp_path):
        """Mutating the returned dict must not affect the cache."""
        import server as server_module
        server_module.init_data_dir(str(tmp_path / "data"))
        self._write_tokens(server_module, {"tokens": {"k": {"name": "t"}}})

        first = server_module.load_tokens()
        first["evil"] = {"name": "injected"}
        first.pop("k", None)

        second = server_module.load_tokens()
        assert "k" in second, "cache was poisoned by caller mutation"
        assert "evil" not in second

    # --- error handling ---

    def test_cache_returns_empty_when_file_missing(self, tmp_path):
        import server as server_module
        server_module.init_data_dir(str(tmp_path / "data"))
        # No tokens.json written
        assert server_module.load_tokens() == {}

    def test_cache_handles_file_disappearing(self, tmp_path):
        import server as server_module
        server_module.init_data_dir(str(tmp_path / "data"))
        self._write_tokens(server_module, {"tokens": {"k": {"name": "t"}}})
        assert "k" in server_module.load_tokens()
        os.remove(server_module.TOKENS_FILE)
        assert server_module.load_tokens() == {}
        assert server_module._tokens_cache_mtime == -1.0

    def test_cache_handles_corrupted_json(self, tmp_path):
        import server as server_module
        server_module.init_data_dir(str(tmp_path / "data"))
        with open(server_module.TOKENS_FILE, 'w') as f:
            f.write("{not valid json")
        # load_json_file logs warning and returns {} → cache stores {}
        assert server_module.load_tokens() == {}

    # --- edge cases ---

    def test_cache_invalidated_on_mtime_change(self, tmp_path):
        import server as server_module
        server_module.init_data_dir(str(tmp_path / "data"))
        self._write_tokens(server_module, {"tokens": {"old": {"name": "old"}}})
        first = server_module.load_tokens()
        assert "old" in first

        # Bump mtime by sleeping enough to overcome FS resolution
        time.sleep(0.05)
        self._write_tokens(server_module, {"tokens": {"new": {"name": "new"}}})

        second = server_module.load_tokens()
        assert "new" in second
        assert "old" not in second

    def test_init_data_dir_resets_cache(self, tmp_path):
        import server as server_module
        server_module.init_data_dir(str(tmp_path / "first"))
        self._write_tokens(server_module, {"tokens": {"k": {"name": "t"}}})
        assert "k" in server_module.load_tokens()

        # Move to a fresh data dir — cache must reset, otherwise we'd
        # still see the stale token from the previous TOKENS_FILE.
        server_module.init_data_dir(str(tmp_path / "second"))
        assert server_module._tokens_cache == {}
        assert server_module._tokens_cache_mtime == -1.0
        assert server_module.load_tokens() == {}

    # --- tricky-timed ---

    def test_concurrent_cache_reads_no_corruption(self, tmp_path):
        import server as server_module
        import concurrent.futures

        server_module.init_data_dir(str(tmp_path / "data"))
        self._write_tokens(server_module, {
            "tokens": {f"tok{i}": {"name": f"n{i}"} for i in range(10)}
        })

        def reader():
            tokens = server_module.load_tokens()
            return len(tokens) == 10 and all(f"tok{i}" in tokens for i in range(10))

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(lambda _: reader(), range(100)))
        assert all(results)

    def test_concurrent_cache_invalidation_no_500(self, tmp_path):
        """Reader threads tolerate a writer atomically replacing the file."""
        import server as server_module
        import concurrent.futures

        server_module.init_data_dir(str(tmp_path / "data"))
        self._write_tokens(server_module, {"tokens": {"v1": {"name": "v1"}}})

        stop = threading.Event()

        def writer():
            i = 0
            while not stop.is_set():
                try:
                    self._write_tokens(server_module,
                                       {"tokens": {f"v{i}": {"name": f"v{i}"}}})
                except (FileNotFoundError, PermissionError, OSError):
                    return  # tmp_path teardown — writer must exit cleanly
                i += 1
                time.sleep(0.005)

        writer_thread = threading.Thread(target=writer, daemon=True)
        writer_thread.start()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                # Each reader just needs to see *some* valid dict — any
                # transient state is acceptable, partial / corrupted is not.
                for _ in range(200):
                    fut = ex.submit(server_module.load_tokens)
                    tokens = fut.result(timeout=2.0)
                    assert isinstance(tokens, dict)
                    for v in tokens.values():
                        assert "name" in v
        finally:
            stop.set()
            writer_thread.join(timeout=2.0)


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
            _stop_server(httpd)

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
            _stop_server(httpd)

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
            _stop_server(httpd)

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
            _stop_server(httpd)

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


class _WatchdogStop(BaseException):
    """Internal sentinel: clean unwind of the watchdog loop in tests."""


class TestWatchdog:
    """In-process watchdog: poll /healthz, os._exit(1) on repeated failure.

    All tests stub out http.client.HTTPConnection and os._exit so they
    don't make real network calls and don't actually kill pytest.
    """

    def _patch_exit(self, monkeypatch, server_module):
        """Replace os._exit with a recording stub; return the call list.

        Also stubs time.sleep so it raises after the fake exit fires —
        this is how we let the watchdog loop unwind without leaving the
        thread running into pytest's teardown.
        """
        calls = []
        triggered = threading.Event()

        def fake_exit(code):
            calls.append(code)
            triggered.set()
            # Returning normally lets the loop resume; the sleep stub
            # below intercepts the next iteration.

        monkeypatch.setattr(server_module.os, '_exit', fake_exit)

        original_sleep = server_module.time.sleep
        def stop_after_exit(secs):
            if triggered.is_set():
                # Break the loop deterministically so the daemon thread
                # exits cleanly and pytest's threadexception plugin
                # doesn't flag it.
                raise _WatchdogStop()
            original_sleep(min(secs, 0.01))
        monkeypatch.setattr(server_module.time, 'sleep', stop_after_exit)
        return calls

    def _patch_httpconnection(self, monkeypatch, server_module, behaviors):
        """Stub http.client.HTTPConnection to return a sequence of fake responses.

        `behaviors` is a list of items consumed in order:
          - int N → respond with status N, body b''
          - Exception subclass → raise that exception in request()
        Once exhausted, behaviors[-1] repeats.
        """
        idx = [0]

        class FakeResp:
            def __init__(self, status):
                self.status = status
            def read(self):
                return b''

        class FakeConn:
            def __init__(self, host, port, timeout=None):
                pass
            def request(self, method, path):
                i = min(idx[0], len(behaviors) - 1)
                idx[0] += 1
                b = behaviors[i]
                if isinstance(b, int):
                    self._next_status = b
                else:
                    raise b
            def getresponse(self):
                return FakeResp(self._next_status)
            def close(self):
                pass

        monkeypatch.setattr(server_module.http.client, 'HTTPConnection', FakeConn)

    def _run_watchdog(self, server_module, *, threshold):
        """Run watchdog in a thread, swallow _WatchdogStop on exit."""
        def runner():
            try:
                server_module._watchdog_loop(8080, 0, threshold, 1)
            except _WatchdogStop:
                pass

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        t.join(timeout=2.0)
        assert not t.is_alive(), "watchdog thread did not stop"

    # --- happy path ---

    def test_resets_failures_on_200(self, monkeypatch):
        """Sustained 200 responses → no exit."""
        import server as server_module
        # Don't use _patch_exit here — we want sleep limited by iteration count,
        # not by exit being triggered (which never happens in this test).
        calls = []
        monkeypatch.setattr(server_module.os, '_exit', lambda code: calls.append(code))
        self._patch_httpconnection(monkeypatch, server_module, [200])

        # Stop after a fixed number of iterations.
        sleep_count = [0]
        def limited_sleep(secs):
            sleep_count[0] += 1
            if sleep_count[0] >= 20:
                raise _WatchdogStop
        monkeypatch.setattr(server_module.time, 'sleep', limited_sleep)

        try:
            server_module._watchdog_loop(8080, 0, threshold=3, timeout=1)
        except _WatchdogStop:
            pass
        assert calls == []  # never tripped

    # --- error handling ---

    def test_exits_after_threshold_connection_refused(self, monkeypatch):
        import server as server_module
        exit_calls = self._patch_exit(monkeypatch, server_module)
        self._patch_httpconnection(monkeypatch, server_module,
                                   [ConnectionRefusedError("nope")])
        self._run_watchdog(server_module, threshold=3)
        assert exit_calls == [1]

    def test_exits_on_503(self, monkeypatch):
        import server as server_module
        exit_calls = self._patch_exit(monkeypatch, server_module)
        self._patch_httpconnection(monkeypatch, server_module, [503])
        self._run_watchdog(server_module, threshold=3)
        assert exit_calls == [1]

    def test_exits_on_500(self, monkeypatch):
        import server as server_module
        exit_calls = self._patch_exit(monkeypatch, server_module)
        self._patch_httpconnection(monkeypatch, server_module, [500])
        self._run_watchdog(server_module, threshold=3)
        assert exit_calls == [1]

    def test_exits_on_socket_timeout(self, monkeypatch):
        import server as server_module
        exit_calls = self._patch_exit(monkeypatch, server_module)
        self._patch_httpconnection(monkeypatch, server_module,
                                   [socket.timeout("idle")])
        self._run_watchdog(server_module, threshold=3)
        assert exit_calls == [1]

    # --- edge cases ---

    def test_recovers_after_intermittent_failure(self, monkeypatch):
        """fail, fail, ok, fail, fail → no exit (only 2 consecutive at end)."""
        import server as server_module
        calls = []
        monkeypatch.setattr(server_module.os, '_exit', lambda code: calls.append(code))
        self._patch_httpconnection(monkeypatch, server_module,
                                   [503, 503, 200, 503, 503, 200])

        sleep_count = [0]
        def limited_sleep(secs):
            sleep_count[0] += 1
            if sleep_count[0] >= 10:
                raise _WatchdogStop
        monkeypatch.setattr(server_module.time, 'sleep', limited_sleep)

        try:
            server_module._watchdog_loop(8080, 0, threshold=3, timeout=1)
        except _WatchdogStop:
            pass
        assert calls == []  # never reached 3 consecutive

    def test_failures_below_threshold_dont_exit(self, monkeypatch):
        """threshold=3, exactly 2 failures + ok → no exit."""
        import server as server_module
        calls = []
        monkeypatch.setattr(server_module.os, '_exit', lambda code: calls.append(code))
        self._patch_httpconnection(monkeypatch, server_module,
                                   [503, 503, 200])

        sleep_count = [0]
        def limited_sleep(secs):
            sleep_count[0] += 1
            if sleep_count[0] >= 5:
                raise _WatchdogStop
        monkeypatch.setattr(server_module.time, 'sleep', limited_sleep)

        try:
            server_module._watchdog_loop(8080, 0, threshold=3, timeout=1)
        except _WatchdogStop:
            pass
        assert calls == []

    def test_logs_to_stderr_on_failure(self, monkeypatch, capsys):
        import server as server_module
        self._patch_exit(monkeypatch, server_module)
        self._patch_httpconnection(monkeypatch, server_module, [503])
        self._run_watchdog(server_module, threshold=3)
        err = capsys.readouterr().err
        assert "watchdog" in err
        assert "503" in err or "failure" in err.lower()

    # --- tricky-timed ---

    def test_flushes_streams_before_exit(self, monkeypatch):
        """sys.stderr.flush and sys.stdout.flush are called before os._exit."""
        import server as server_module
        flush_calls = []
        original_stderr_flush = sys.stderr.flush
        original_stdout_flush = sys.stdout.flush

        def stderr_flush():
            flush_calls.append('stderr')
            original_stderr_flush()
        def stdout_flush():
            flush_calls.append('stdout')
            original_stdout_flush()

        # Patch flush methods on the actual sys streams used by the module.
        monkeypatch.setattr(server_module.sys.stderr, 'flush', stderr_flush)
        monkeypatch.setattr(server_module.sys.stdout, 'flush', stdout_flush)

        self._patch_exit(monkeypatch, server_module)
        self._patch_httpconnection(monkeypatch, server_module, [503])
        self._run_watchdog(server_module, threshold=3)
        assert 'stderr' in flush_calls
        assert 'stdout' in flush_calls


class TestHealthz:
    """GET /healthz — liveness probe, no auth, no rate limit."""

    # --- happy path ---

    def test_healthz_returns_200_with_payload(self, running_server):
        status, data = get_json(running_server, "/healthz")
        assert status == 200
        assert data["status"] == "ok"
        assert isinstance(data["workers"], int)
        assert data["workers"] >= 1
        assert isinstance(data["queue_depth"], int)
        assert data["queue_depth"] >= 0
        assert isinstance(data["uptime_seconds"], int)
        assert data["uptime_seconds"] >= 0

    def test_healthz_uptime_grows(self, running_server):
        _, first = get_json(running_server, "/healthz")
        time.sleep(1.1)
        _, second = get_json(running_server, "/healthz")
        assert second["uptime_seconds"] >= first["uptime_seconds"] + 1

    # --- error handling ---

    def test_healthz_post_is_404(self, running_server):
        status, _ = post_json(running_server, "/healthz", {})
        assert status == 404

    # --- edge cases ---

    def test_healthz_bypasses_rate_limit(self, rate_limited_server):
        """rate_limit=3 in fixture; 10× /healthz should not ban the IP."""
        for _ in range(10):
            status, data = get_json(rate_limited_server, "/healthz")
            assert status == 200
        # And a regular request still works (bucket not consumed by /healthz).
        status, data = post_json(rate_limited_server, "/increment",
                                 {"project_key": "after-healthz"})
        assert status == 200

    def test_healthz_works_under_partial_load(self, tmp_path, monkeypatch):
        """One worker busy, /healthz still served by another."""
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True, max_workers=2)
        try:
            slow = _open_slow_socket(url)
            time.sleep(0.1)
            try:
                status, data = get_json(url, "/healthz")
                assert status == 200
                assert data["status"] == "ok"
            finally:
                slow.close()
        finally:
            _stop_server(httpd)

    # --- tricky-timed ---

    def test_healthz_workers_count_correct(self, tmp_path, monkeypatch):
        """healthz reports the configured worker count to loopback callers."""
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True, max_workers=7)
        try:
            status, data = get_json(url, "/healthz")
            assert status == 200
            assert data["workers"] == 7
            # queue_depth is 0 when there is no other traffic.
            assert data["queue_depth"] == 0
        finally:
            _stop_server(httpd)

    def test_healthz_hides_internals_from_non_loopback(self, tmp_path, monkeypatch):
        """Non-loopback callers see status+uptime only — no queue_depth oracle."""
        import server as server_module

        # Pretend every incoming connection comes from a public IP by
        # overriding what the handler sees. Patching client_address on
        # the class level reaches both /healthz and the rate-limit path.
        original_setup = server_module.BuildNumberHandler.setup
        def fake_setup(self):
            original_setup(self)
            # Replace after super().setup() so socket bookkeeping is intact.
            self.client_address = ('203.0.113.7', 12345)
        monkeypatch.setattr(server_module.BuildNumberHandler, 'setup', fake_setup)

        url, httpd = _start_server(tmp_path, monkeypatch, accept=True)
        try:
            status, data = get_json(url, "/healthz")
            assert status == 200
            assert data["status"] == "ok"
            assert "uptime_seconds" in data
            # Internals must not leak.
            assert "workers" not in data
            assert "queue_depth" not in data
        finally:
            _stop_server(httpd)

    def test_healthz_rate_limited_for_non_loopback(self, tmp_path, monkeypatch):
        """Non-loopback callers are subject to rate limiting on /healthz."""
        import server as server_module

        # Spoof a public IP for every request.
        original_setup = server_module.BuildNumberHandler.setup
        def fake_setup(self):
            original_setup(self)
            self.client_address = ('203.0.113.7', 12345)
        monkeypatch.setattr(server_module.BuildNumberHandler, 'setup', fake_setup)

        url, httpd = _start_server(tmp_path, monkeypatch, accept=True)
        # Reset rate-limit state and turn it on tightly.
        monkeypatch.setattr(server_module, 'rate_limit', 2)
        monkeypatch.setattr(server_module, 'rate_tracker', {})
        monkeypatch.setattr(server_module, 'temp_bans', {})
        monkeypatch.setattr(server_module, 'permanent_bans', set())
        try:
            # First 2 succeed, third gets 429 (or RST under early-error).
            for _ in range(2):
                status, _ = get_json(url, "/healthz")
                assert status == 200
            third = _expect_rejection(get_json, url, "/healthz")
            if third != _SERVER_REJECTED:
                status, _ = third
                assert status == 429
        finally:
            _stop_server(httpd)


class TestSocketTimeout:
    """Socket-level timeout via BuildNumberHandler.timeout — Slowloris defense."""

    def _send_partial_request(self, url, content_length=100, body=b''):
        """Open TCP, send headers (and optionally part of body), do not close.

        Returns the socket so the test can read the server's response.
        """
        parsed = urlparse(url)
        s = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        headers = (
            f"POST /increment HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {content_length}\r\n"
            f"\r\n"
        )
        s.sendall(headers.encode('ascii'))
        if body:
            s.sendall(body)
        return s

    def _read_status_line(self, sock, deadline_sec):
        """Read until we get the HTTP status line or timeout."""
        sock.settimeout(deadline_sec)
        chunks = []
        try:
            while b"\r\n" not in b"".join(chunks):
                data = sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
                if len(chunks) > 1024:
                    break  # paranoia
        except (socket.timeout, OSError):
            pass
        raw = b"".join(chunks)
        if not raw:
            return None
        return raw.split(b"\r\n", 1)[0].decode('ascii', errors='replace')

    # --- error handling ---

    def test_partial_body_returns_408(self, tmp_path, monkeypatch):
        """Headers sent, body never arrives → server times out → 408."""
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True,
                                   handler_timeout=1)
        try:
            s = self._send_partial_request(url, content_length=100)
            try:
                status_line = self._read_status_line(s, deadline_sec=4.0)
                # Either 408 reply or RST — both are acceptable Slowloris
                # defenses. We assert that something arrived OR the
                # connection was reset.
                if status_line is not None:
                    assert "408" in status_line, f"expected 408, got {status_line!r}"
            finally:
                s.close()
        finally:
            _stop_server(httpd)

    def test_408_response_is_well_formed_json(self, tmp_path, monkeypatch):
        """When the 408 reaches the client, body is valid JSON with 'error'."""
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True,
                                   handler_timeout=1)
        try:
            parsed = urlparse(url)
            s = self._send_partial_request(url, content_length=100)
            try:
                s.settimeout(4.0)
                chunks = []
                try:
                    while True:
                        data = s.recv(4096)
                        if not data:
                            break
                        chunks.append(data)
                except (socket.timeout, OSError):
                    pass
                raw = b"".join(chunks)
                if not raw:
                    return  # RST instead of response — see partial_body test
                head, _, body = raw.partition(b"\r\n\r\n")
                head_text = head.decode('ascii', errors='replace')
                if "408" not in head_text.split("\r\n", 1)[0]:
                    return
                assert "application/json" in head_text.lower()
                payload = json.loads(body.decode('utf-8'))
                assert "error" in payload
                assert "timeout" in payload["error"].lower()
            finally:
                s.close()
        finally:
            _stop_server(httpd)

    def test_dead_socket_after_timeout_doesnt_crash_worker(self, tmp_path, monkeypatch):
        """Client closing socket before 408 arrives → worker recovers cleanly."""
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True,
                                   handler_timeout=1)
        try:
            # Send headers, then immediately drop the connection.
            s = self._send_partial_request(url, content_length=100)
            s.close()
            time.sleep(1.5)  # let worker hit timeout / cleanup

            # Server still serves new requests.
            status, _ = post_json(url, "/increment", {"project_key": "alive"})
            assert status == 200
        finally:
            _stop_server(httpd)

    # --- edge cases ---

    def test_timeout_none_disables(self, tmp_path, monkeypatch):
        """timeout=None means socket has no idle timeout (regression guard).

        We verify behaviorally: with timeout=None, a 0.6s pause between
        headers and body still completes successfully (no 408).
        """
        # Default fixture sets timeout=None — exactly what we want.
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True)

        try:
            parsed = urlparse(url)
            body = b'{"project_key":"slow"}'
            s = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
            try:
                headers = (
                    f"POST /increment HTTP/1.1\r\n"
                    f"Host: {parsed.hostname}:{parsed.port}\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                )
                s.sendall(headers.encode('ascii'))
                time.sleep(0.6)  # would trigger 408 with timeout=0.5
                s.sendall(body)
                s.settimeout(4.0)
                resp = b""
                try:
                    while True:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        resp += chunk
                except (socket.timeout, OSError):
                    pass
                first = resp.split(b"\r\n", 1)[0].decode('ascii', errors='replace')
                assert "200" in first, f"expected 200, got {first!r}"
            finally:
                s.close()
        finally:
            _stop_server(httpd)

    # --- tricky-timed ---

    def test_header_slowloris_killed_by_wall_clock_deadline(self, tmp_path, monkeypatch):
        """Drip-feeding HEADERS just under per-recv timeout still gets killed.

        This is the canonical Slowloris attack: send one byte every
        (timeout - epsilon) seconds. The per-recv timeout never trips,
        but the wall-clock max_request_seconds deadline does, forcing
        the worker to release the socket.
        """
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True,
                                   handler_timeout=1, max_request_seconds=1)
        try:
            parsed = urlparse(url)
            s = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
            try:
                # Send the request line, then drip-feed header bytes
                # 0.4 s apart. Per-recv (1 s) never trips. After the
                # 1 s wall-clock deadline, the worker shuts the socket
                # down — recv returns empty / OSError on our side.
                s.sendall(b"POST /increment HTTP/1.1\r\n")
                start = time.monotonic()
                killed = False
                # Try to send slowly for up to 5 seconds. Server should
                # close us well before then.
                for i in range(20):
                    try:
                        s.sendall(b"X-Pad: y\r\n")
                    except OSError:
                        killed = True
                        break
                    elapsed = time.monotonic() - start
                    if elapsed > 4.5:
                        break
                    time.sleep(0.4)

                # Either send raised OSError, or recv returns no data /
                # EOF because the server shut the socket down.
                s.settimeout(2.0)
                try:
                    data = s.recv(4096)
                    # Empty recv means peer closed (FIN).
                    closed = (data == b'')
                except (OSError, socket.timeout):
                    closed = True
                assert killed or closed, "Server did not enforce wall-clock deadline"
                # And it must have happened within the deadline window.
                assert time.monotonic() - start < 4.0, \
                    "Server took too long to enforce deadline"
            finally:
                s.close()
        finally:
            _stop_server(httpd)

    def test_chunked_body_within_timeout_succeeds(self, tmp_path, monkeypatch):
        """Body sent in chunks slower than the timeout but each chunk
        arrives in time → request succeeds (timeout is per-recv, not
        end-to-end)."""
        # 1s idle timeout. We send with 0.05s gaps — each gap < 1s, so no 408.
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True,
                                   handler_timeout=1)
        try:
            parsed = urlparse(url)
            body = b'{"project_key":"chunked"}'
            s = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
            try:
                headers = (
                    f"POST /increment HTTP/1.1\r\n"
                    f"Host: {parsed.hostname}:{parsed.port}\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                )
                s.sendall(headers.encode('ascii'))
                # Send body byte-by-byte with 0.05s gap (well below 1s).
                for byte in body:
                    s.sendall(bytes([byte]))
                    time.sleep(0.05)

                s.settimeout(4.0)
                resp = b""
                try:
                    while True:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        resp += chunk
                except (socket.timeout, OSError):
                    pass
                first = resp.split(b"\r\n", 1)[0].decode('ascii', errors='replace')
                assert "200" in first, f"got {first!r}"
            finally:
                s.close()
        finally:
            _stop_server(httpd)


class TestPooledHTTPServer:
    """Worker thread pool, bounded queue, 503 backpressure, daemon workers."""

    # --- happy path ---

    def test_basic_request_through_pool(self, running_server):
        """Normal request still works through the worker pool (regression)."""
        status, data = post_json(running_server, "/increment", {"project_key": "t"})
        assert status == 200
        assert data["build_number"] == 1

    def test_concurrent_increments_no_lost_updates(self, tmp_path, monkeypatch):
        """100 concurrent increments produce build numbers 1..100, no duplicates.

        Spins up a server with enough workers + queue (max_workers=16 → 32
        in-flight capacity) so the test does not collide with the server's
        own 503 backpressure. We are testing the lock, not the pool.
        """
        import concurrent.futures

        url, httpd = _start_server(tmp_path, monkeypatch, accept=True, max_workers=16)
        try:
            N = 100
            # Client concurrency capped at server capacity; clients beyond
            # that block at TCP accept until a server slot frees, which is
            # the desired backpressure behavior.
            CLIENT_CONCURRENCY = 16

            def worker(_):
                return post_json(url, "/increment", {"project_key": "race"})

            with concurrent.futures.ThreadPoolExecutor(max_workers=CLIENT_CONCURRENCY) as ex:
                results = list(ex.map(worker, range(N)))

            statuses = [r[0] for r in results]
            assert all(s == 200 for s in statuses), f"got non-200: {statuses}"
            numbers = sorted(r[1]["build_number"] for r in results)
            assert numbers == list(range(1, N + 1))
        finally:
            _stop_server(httpd)

    def test_concurrent_distinct_projects(self, tmp_path, monkeypatch):
        """Multiple project keys are isolated under concurrent load."""
        import concurrent.futures

        url, httpd = _start_server(tmp_path, monkeypatch, accept=True, max_workers=8)
        try:
            keys = ["a", "b", "c", "d"]
            per_key = 5

            def worker(idx):
                key = keys[idx % len(keys)]
                return key, post_json(url, "/increment", {"project_key": key})

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                results = list(ex.map(worker, range(per_key * len(keys))))

            per_key_numbers = {k: [] for k in keys}
            for key, (status, data) in results:
                assert status == 200
                per_key_numbers[key].append(data["build_number"])
            for k, nums in per_key_numbers.items():
                assert sorted(nums) == list(range(1, per_key + 1))
        finally:
            _stop_server(httpd)

    # --- error handling ---

    def _read_503_or_rst(self, url, path='/'):
        """Open a raw socket, send a complete request, expect 503 or RST.

        Avoids http.client's two-phase send (headers then body) which on
        Windows races with our immediate close after writing 503 →
        ConnectionAbortedError instead of a parsable response. We send
        everything in a single sendall() so the server has the full request
        before it decides to refuse.
        """
        parsed = urlparse(url)
        s = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        try:
            req = (
                f"{('GET' if path == '/' else 'POST')} {path} HTTP/1.1\r\n"
                f"Host: {parsed.hostname}:{parsed.port}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: 0\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            )
            s.sendall(req.encode('ascii'))
            # Read until EOF.
            chunks = []
            s.settimeout(3.0)
            try:
                while True:
                    data = s.recv(4096)
                    if not data:
                        break
                    chunks.append(data)
            except (socket.timeout, OSError):
                pass
            return b''.join(chunks)
        finally:
            try:
                s.close()
            except OSError:
                pass

    def test_503_when_queue_full(self, tmp_path, monkeypatch):
        """All worker+queue slots taken by slow connections → 503 or RST."""
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True, max_workers=1)
        try:
            # 2 slow sockets: 1 occupies worker (blocks reading body),
            # 1 occupies the queue slot (max_workers == queue size == 1).
            slow = [_open_slow_socket(url) for _ in range(2)]
            time.sleep(0.2)

            # Third request — accept either 503 or empty RST (TCP race on
            # Windows; ADR 001 documents this for early-error responses).
            response = self._read_503_or_rst(url)
            if response:
                first_line = response.split(b"\r\n", 1)[0].decode('ascii', 'replace')
                assert "503" in first_line, f"expected 503 status, got {first_line!r}"

            for s in slow:
                try:
                    s.close()
                except OSError:
                    pass
        finally:
            _stop_server(httpd)

    def test_overload_closes_connection_immediately(self, tmp_path, monkeypatch):
        """Refused-overload path closes the socket without writing a body.

        Writing a 503 body from the accept thread (even with a 100ms send
        timeout) steals capacity from accept() under sustained overload,
        defeating the bounded-queue design. We instead close the socket
        immediately; clients see RST/FIN. This test confirms that
        behavior — no HTTP response bytes arrive, the recv just ends.
        """
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True, max_workers=1)
        try:
            slow = [_open_slow_socket(url) for _ in range(2)]
            time.sleep(0.2)

            response = self._read_503_or_rst(url)
            # Empty response body = clean close. Some platforms might
            # surface kernel buffers — accept either, just not a 503 body.
            if response:
                # On the off chance any bytes arrive, they must NOT include
                # an HTTP/1.1 status line (we do not write one anymore).
                assert b"HTTP/1.1" not in response[:20], \
                    f"unexpected HTTP response from refused socket: {response[:100]!r}"

            for s in slow:
                s.close()
        finally:
            _stop_server(httpd)

    def test_handle_error_silences_connection_reset(self, running_server):
        """Client closing socket mid-request must not log a traceback."""
        parsed = urlparse(running_server)
        # Open and close immediately with no data — server may get
        # ConnectionResetError or similar inside the worker.
        s = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        s.sendall(b"POST /increment HTTP/1.1\r\nHost: x\r\nContent-Length: 50\r\n\r\n")
        s.close()
        time.sleep(0.2)

        # Server still alive and serving:
        status, _ = post_json(running_server, "/increment", {"project_key": "still-alive"})
        assert status == 200

    # --- edge cases ---

    def test_workers_are_daemon(self, running_server):
        """All cbnc-worker-* threads must be daemon."""
        cbnc_workers = [
            t for t in threading.enumerate()
            if t.name.startswith('cbnc-worker-')
        ]
        assert cbnc_workers, "no worker threads found"
        for t in cbnc_workers:
            assert t.daemon, f"{t.name} is not daemon"

    def test_workers_count_matches_max_workers(self, tmp_path, monkeypatch):
        """Pool starts exactly max_workers worker threads."""
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True, max_workers=7)
        try:
            assert len(httpd._workers) == 7
            assert all(t.is_alive() for t in httpd._workers)
        finally:
            _stop_server(httpd)

    def test_server_close_idempotent(self, tmp_path, monkeypatch):
        """Calling shutdown+server_close twice does not raise."""
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True, max_workers=2)
        _stop_server(httpd)
        # Second call: shutdown raises RuntimeError because serve_forever
        # already exited; we tolerate that via the helper's contract,
        # but server_close must remain idempotent on its own.
        httpd.server_close()  # must not raise

    def test_get_root_works_via_pool(self, running_server):
        """GET / still returns service info through the worker pool."""
        status, data = get_json(running_server, "/")
        assert status == 200
        assert "service" in data

    # --- tricky-timed ---

    def test_503_then_normal_recovers(self, tmp_path, monkeypatch):
        """After overload subsides, the pool returns to normal operation."""
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True, max_workers=1)
        try:
            slow = [_open_slow_socket(url) for _ in range(2)]
            time.sleep(0.2)

            # Confirm 503 right now.
            r = _expect_rejection(post_json, url, "/increment", {"project_key": "x"})
            if r is not _SERVER_REJECTED:
                assert r[0] == 503

            # Close slow connections, give worker a moment to recover.
            for s in slow:
                s.close()
            time.sleep(0.5)

            # Normal request now succeeds.
            status, data = post_json(url, "/increment", {"project_key": "recovered"})
            assert status == 200
            assert data["build_number"] == 1
        finally:
            _stop_server(httpd)


class TestTOCTOUAndDiscriminatedResults:
    """The TOCTOU-safe approval/limit/increment dispatch from Commit 1.

    Concurrent tests live here too (race regressions are the whole point
    of the discriminated result), even though they exercise the pool.
    """

    # --- happy path ---

    def test_increment_known_project_returns_200(self, strict_server):
        status, data = post_json(strict_server, "/increment",
                                 {"project_key": "approved-project"})
        assert status == 200
        assert data["build_number"] == 1

    def test_set_known_project_returns_200(self, strict_server):
        status, data = post_json(strict_server, "/set",
                                 {"project_key": "approved-project", "version": 7})
        assert status == 200
        assert data["build_number"] == 7

    # --- error handling ---

    def test_increment_unapproved_returns_403(self, strict_server):
        status, data = post_json(strict_server, "/increment",
                                 {"project_key": "new-project"})
        assert status == 403
        assert "not approved" in data["error"]

    def test_increment_limit_reached_returns_507(self, limited_server):
        # limited_server has max_projects=3; create 3 first, then the 4th fails.
        for i in range(3):
            status, _ = post_json(limited_server, "/increment",
                                  {"project_key": f"p{i}"})
            assert status == 200
        status, data = post_json(limited_server, "/increment",
                                 {"project_key": "p3"})
        assert status == 507
        assert "limit" in data["error"].lower()
        assert data["max_projects"] == 3

    def test_set_unapproved_returns_403_in_strict(self, strict_server):
        status, data = post_json(strict_server, "/set",
                                 {"project_key": "unknown-key", "version": 1})
        assert status == 403

    # --- edge cases ---

    def test_set_force_unapproved_via_function(self, tmp_path):
        """Direct call with force_unapproved=True bypasses approval (CLI path)."""
        import server as server_module
        server_module.init_data_dir(str(tmp_path / "data"))
        with open(server_module.BUILD_NUMBERS_FILE, 'w') as f:
            json.dump({}, f)

        # Without the flag, accept_unknown=False → unapproved.
        try:
            old_accept = server_module.accept_unknown
            server_module.accept_unknown = False
            result = server_module.set_build_number("brand-new", 42)
            assert result.status is server_module._SetStatus.UNAPPROVED

            # With force_unapproved=True, the CLI must succeed regardless.
            result = server_module.set_build_number("brand-new", 42,
                                                    force_unapproved=True)
            assert result.status is server_module._SetStatus.OK
            assert result.build_number == 42
        finally:
            server_module.accept_unknown = old_accept

    def test_increment_local_version_higher_used(self, running_server):
        # First sets server to 5.
        status, data = post_json(running_server, "/increment",
                                 {"project_key": "lv", "local_version": 5})
        assert status == 200
        assert data["build_number"] == 6  # max(5, 0) + 1 = 6

    def test_increment_local_version_lower_ignored(self, running_server):
        post_json(running_server, "/increment", {"project_key": "lv2"})
        post_json(running_server, "/increment", {"project_key": "lv2"})  # now 2
        status, data = post_json(running_server, "/increment",
                                 {"project_key": "lv2", "local_version": 1})
        assert status == 200
        assert data["build_number"] == 3  # max(0/lower, 2) + 1

    def test_existing_project_increments_when_at_limit(self, tmp_path, monkeypatch):
        """Already-known project still increments even when max_projects is hit."""
        import server as server_module

        url, httpd = _start_server(
            tmp_path, monkeypatch, accept=True,
            initial_data={"a": 1, "b": 2, "c": 3, "d": 4, "e": 5},
        )
        try:
            monkeypatch.setattr(server_module, 'max_projects', 3)
            # Existing project — limit must not apply.
            status, data = post_json(url, "/increment", {"project_key": "a"})
            assert status == 200
            assert data["build_number"] == 2
            # New project — rejected.
            status, _ = post_json(url, "/increment", {"project_key": "f"})
            assert status == 507
        finally:
            _stop_server(httpd)

    # --- tricky-timed (concurrency) ---

    def test_concurrent_new_projects_limit_respected(self, tmp_path, monkeypatch):
        """Under concurrent load, project limit is never exceeded."""
        import concurrent.futures
        import server as server_module

        # max_workers=20 + queue=20 → 40 in-flight capacity, so the test's
        # 20 simultaneous clients all reach the handler (no 503 noise).
        url, httpd = _start_server(tmp_path, monkeypatch, accept=True, max_workers=20)
        try:
            monkeypatch.setattr(server_module, 'max_projects', 5)

            N = 20

            def worker(i):
                return post_json(url, "/increment", {"project_key": f"p{i}"})

            with concurrent.futures.ThreadPoolExecutor(max_workers=N) as ex:
                results = list(ex.map(worker, range(N)))

            ok = sum(1 for s, _ in results if s == 200)
            limit = sum(1 for s, _ in results if s == 507)
            assert ok == 5, f"expected 5 success, got {ok}"
            assert limit == 15, f"expected 15 rejected, got {limit}"
        finally:
            _stop_server(httpd)
