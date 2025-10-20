import json
import os
from unittest.mock import patch, MagicMock

import pytest

import client


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
