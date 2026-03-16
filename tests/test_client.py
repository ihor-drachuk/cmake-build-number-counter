import io
import json
import os
import urllib.error
from unittest.mock import patch, MagicMock

import pytest

import client
from client import ServerRejectedError


class TestLoadLocalCounter:
    def test_missing_file(self, tmp_local_file):
        assert client.load_local_counter(tmp_local_file) == 0

    def test_valid_file(self, tmp_local_file):
        with open(tmp_local_file, 'w') as f:
            f.write("42")
        assert client.load_local_counter(tmp_local_file) == 42

    def test_empty_file(self, tmp_local_file):
        with open(tmp_local_file, 'w') as f:
            f.write("")
        assert client.load_local_counter(tmp_local_file) == 0

    def test_corrupt_file(self, tmp_local_file):
        with open(tmp_local_file, 'w') as f:
            f.write("not_a_number")
        assert client.load_local_counter(tmp_local_file) == 0


class TestSaveLocalCounter:
    def test_creates_file(self, tmp_local_file):
        client.save_local_counter(tmp_local_file, 7)
        with open(tmp_local_file) as f:
            assert f.read().strip() == "7"

    def test_overwrites(self, tmp_local_file):
        client.save_local_counter(tmp_local_file, 5)
        client.save_local_counter(tmp_local_file, 10)
        with open(tmp_local_file) as f:
            assert f.read().strip() == "10"

    def test_creates_parent_directory(self, tmp_path):
        path = str(tmp_path / "subdir" / "counter.txt")
        client.save_local_counter(path, 3)
        with open(path) as f:
            assert f.read().strip() == "3"


class TestSyncState:
    def test_roundtrip(self, tmp_local_file):
        client.save_local_sync_state(tmp_local_file, 15)
        assert client.load_local_sync_state(tmp_local_file) == 15

    def test_missing_returns_none(self, tmp_local_file):
        assert client.load_local_sync_state(tmp_local_file) is None

    def test_clear(self, tmp_local_file):
        client.save_local_sync_state(tmp_local_file, 5)
        client.clear_local_sync_state(tmp_local_file)
        assert client.load_local_sync_state(tmp_local_file) is None

    def test_clear_nonexistent(self, tmp_local_file):
        # Should not raise
        client.clear_local_sync_state(tmp_local_file)


class TestIncrementLocally:
    def test_from_zero(self, tmp_local_file):
        result = client.increment_locally(tmp_local_file, "test")
        assert result == 1
        assert client.load_local_counter(tmp_local_file) == 1
        assert client.load_local_sync_state(tmp_local_file) == 1

    def test_from_existing(self, tmp_local_file):
        client.save_local_counter(tmp_local_file, 5)
        result = client.increment_locally(tmp_local_file, "test")
        assert result == 6


class TestFormatOutput:
    def test_plain(self):
        assert client.format_output(42, 'plain', 'proj') == "42"

    def test_cmake(self):
        assert client.format_output(42, 'cmake', 'proj') == 'set(BUILD_NUMBER "42")'

    def test_json(self):
        result = json.loads(client.format_output(42, 'json', 'proj'))
        assert result == {'build_number': 42, 'project_key': 'proj'}


class TestGetBuildNumber:
    def test_no_server_uses_local(self, tmp_local_file):
        build_num, was_local = client.get_build_number("test", server_url=None, local_file=tmp_local_file)
        assert build_num == 1
        assert was_local is True

    def test_increments_locally(self, tmp_local_file):
        client.get_build_number("test", server_url=None, local_file=tmp_local_file)
        build_num, _ = client.get_build_number("test", server_url=None, local_file=tmp_local_file)
        assert build_num == 2


class TestProjectKeyValidation:
    def test_invalid_key_raises(self, tmp_local_file):
        with pytest.raises(ValueError):
            client.get_build_number("../../bad", local_file=tmp_local_file)

    def test_valid_key_works(self, tmp_local_file):
        build_num, was_local = client.get_build_number("good-key", local_file=tmp_local_file)
        assert build_num == 1


class TestIncrementOnServer:
    def test_success(self):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"build_number": 42}).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch('client.urllib.request.urlopen', return_value=mock_response):
            result = client.increment_on_server("http://fake:8080", "proj")
        assert result == 42

    def test_connection_error(self):
        import urllib.error
        with patch('client.urllib.request.urlopen', side_effect=urllib.error.URLError("refused")):
            result = client.increment_on_server("http://fake:8080", "proj")
        assert result is None


class TestSetOnServer:
    def test_success(self):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"build_number": 42}).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch('client.urllib.request.urlopen', return_value=mock_response):
            result = client.set_on_server("http://fake:8080", "proj", 42)
        assert result == 42

    def test_connection_error(self):
        import urllib.error
        with patch('client.urllib.request.urlopen', side_effect=urllib.error.URLError("refused")):
            result = client.set_on_server("http://fake:8080", "proj", 42)
        assert result is None

    def test_token_sent_in_header(self):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"build_number": 42}).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch('client.urllib.request.urlopen', return_value=mock_response) as mock_urlopen:
            client.set_on_server("http://fake:8080", "proj", 42, server_token="my-token")

        req = mock_urlopen.call_args[0][0]
        assert req.get_header('Authorization') == 'Bearer my-token'


class TestForceSetBuildNumber:
    def test_force_set_local_only(self, tmp_local_file):
        """Force-set without server updates local counter."""
        result, was_local = client.force_set_build_number(
            "test", 42, server_url=None, local_file=tmp_local_file
        )
        assert result == 42
        assert was_local is True
        assert client.load_local_counter(tmp_local_file) == 42

    def test_force_set_clears_sync_state(self, tmp_local_file):
        """Force-set clears any pending sync state."""
        client.save_local_sync_state(tmp_local_file, 10)
        client.force_set_build_number(
            "test", 5, server_url=None, local_file=tmp_local_file
        )
        assert client.load_local_sync_state(tmp_local_file) is None

    def test_force_set_to_zero(self, tmp_local_file):
        """Force-set to 0 is allowed."""
        client.save_local_counter(tmp_local_file, 50)
        result, _ = client.force_set_build_number(
            "test", 0, server_url=None, local_file=tmp_local_file
        )
        assert result == 0
        assert client.load_local_counter(tmp_local_file) == 0

    def test_force_set_idempotent_counter_file(self, tmp_local_file):
        """Repeated force-set with same value does not rewrite counter file (mtime preserved)."""
        import time
        client.force_set_build_number("test", 42, server_url=None, local_file=tmp_local_file)
        mtime1 = os.path.getmtime(tmp_local_file)
        time.sleep(0.1)
        client.force_set_build_number("test", 42, server_url=None, local_file=tmp_local_file)
        mtime2 = os.path.getmtime(tmp_local_file)
        assert mtime1 == mtime2, f"Same value should not rewrite file: {mtime1} -> {mtime2}"
        # Different value MUST rewrite
        time.sleep(0.1)
        client.force_set_build_number("test", 43, server_url=None, local_file=tmp_local_file)
        mtime3 = os.path.getmtime(tmp_local_file)
        assert mtime3 > mtime2, f"Different value should rewrite file: {mtime2} -> {mtime3}"

    def test_force_set_invalid_key_raises(self, tmp_local_file):
        """Force-set with invalid project key raises ValueError."""
        with pytest.raises(ValueError):
            client.force_set_build_number("../../bad", 5, local_file=tmp_local_file)


class TestIncrementOnServerAuth:
    def test_token_sent_in_header(self):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"build_number": 1}).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch('client.urllib.request.urlopen', return_value=mock_response) as mock_urlopen:
            client.increment_on_server("http://fake:8080", "proj", server_token="my-secret")

        req = mock_urlopen.call_args[0][0]
        assert req.get_header('Authorization') == 'Bearer my-secret'

    def test_no_token_no_header(self):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"build_number": 1}).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch('client.urllib.request.urlopen', return_value=mock_response) as mock_urlopen:
            client.increment_on_server("http://fake:8080", "proj", server_token=None)

        req = mock_urlopen.call_args[0][0]
        assert req.get_header('Authorization') is None


class TestLogMessages:
    """Tests that log messages are appropriate for each scenario."""

    @pytest.fixture(autouse=True)
    def capture_logs(self):
        """Capture log_message() calls into a list."""
        self.logs = []
        original = client.log_message
        client.log_message = lambda msg, **kw: self.logs.append(f"[CBNC] {msg}")
        yield
        client.log_message = original

    def _log_text(self):
        return "\n".join(self.logs)

    def test_no_warning_without_server(self, tmp_local_file):
        """No WARNING when server is not configured (purely local)."""
        client.get_build_number("test", server_url=None, local_file=tmp_local_file)
        assert "WARNING" not in self._log_text()

    def test_no_sync_message_without_server(self, tmp_local_file):
        """No 'will sync to server' when server is not configured."""
        client.get_build_number("test", server_url=None, local_file=tmp_local_file)
        assert "sync" not in self._log_text().lower()

    def test_no_sync_file_without_server(self, tmp_local_file):
        """No .sync file created when server is not configured."""
        client.get_build_number("test", server_url=None, local_file=tmp_local_file)
        assert not os.path.exists(tmp_local_file + ".sync")

    def test_warning_on_server_fallback(self, tmp_local_file):
        """WARNING appears when server is configured but unreachable."""
        client.get_build_number("test", server_url="http://localhost:1",
                                local_file=tmp_local_file)
        assert "WARNING" in self._log_text()

    def test_project_key_in_local_message(self, tmp_local_file):
        """Project key appears in purely-local message."""
        client.get_build_number("my-proj", server_url=None, local_file=tmp_local_file)
        assert "my-proj" in self._log_text()

    def test_project_key_in_server_message(self, tmp_local_file):
        """Project key appears in server success message."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"build_number": 42}).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch('client.urllib.request.urlopen', return_value=mock_response):
            client.get_build_number("my-proj", server_url="http://fake:8080",
                                    local_file=tmp_local_file)
        assert "my-proj" in self._log_text()

    def test_force_set_no_warning_without_server(self, tmp_local_file):
        """Force-set without server: no WARNING."""
        client.force_set_build_number("test", 5, server_url=None,
                                      local_file=tmp_local_file)
        assert "WARNING" not in self._log_text()

    def test_force_set_warning_on_server_fallback(self, tmp_local_file):
        """Force-set with unreachable server: WARNING appears."""
        client.force_set_build_number("test", 5, server_url="http://localhost:1",
                                      local_file=tmp_local_file)
        assert "WARNING" in self._log_text()


def _make_http_error(code, body_dict):
    """Create a urllib HTTPError with a JSON body."""
    body = json.dumps(body_dict).encode('utf-8')
    return urllib.error.HTTPError(
        url="http://fake:8080/increment",
        code=code,
        msg=f"HTTP {code}",
        hdrs={},
        fp=io.BytesIO(body),
    )


class TestServerRejection:
    """Tests that server rejections (401, 403, 429) raise ServerRejectedError
    instead of silently falling back to local counter."""

    @pytest.mark.parametrize("code", [401, 403, 429])
    def test_increment_rejection_raises(self, code):
        error = _make_http_error(code, {"error": f"Rejected with {code}"})
        with patch('client.urllib.request.urlopen', side_effect=error):
            with pytest.raises(ServerRejectedError) as exc_info:
                client.increment_on_server("http://fake:8080", "proj")
            assert exc_info.value.status_code == code
            assert f"Rejected with {code}" in exc_info.value.message

    @pytest.mark.parametrize("code", [401, 403, 429])
    def test_set_rejection_raises(self, code):
        error = _make_http_error(code, {"error": f"Rejected with {code}"})
        with patch('client.urllib.request.urlopen', side_effect=error):
            with pytest.raises(ServerRejectedError) as exc_info:
                client.set_on_server("http://fake:8080", "proj", 42)
            assert exc_info.value.status_code == code

    def test_server_error_500_returns_none(self):
        """Non-rejection HTTP errors (e.g. 500) still return None (transient)."""
        error = _make_http_error(500, {"error": "Internal server error"})
        with patch('client.urllib.request.urlopen', side_effect=error):
            result = client.increment_on_server("http://fake:8080", "proj")
        assert result is None

    def test_get_build_number_rejection_propagates(self, tmp_local_file):
        """ServerRejectedError propagates through get_build_number (no fallback)."""
        with patch('client.increment_on_server',
                   side_effect=ServerRejectedError(429, "Banned")):
            with pytest.raises(ServerRejectedError):
                client.get_build_number("proj", server_url="http://fake:8080",
                                        local_file=tmp_local_file)

    def test_force_set_rejection_propagates(self, tmp_local_file):
        """ServerRejectedError propagates through force_set_build_number (no fallback)."""
        with patch('client.set_on_server',
                   side_effect=ServerRejectedError(401, "Invalid token")):
            with pytest.raises(ServerRejectedError):
                client.force_set_build_number("proj", 42,
                                              server_url="http://fake:8080",
                                              local_file=tmp_local_file)
