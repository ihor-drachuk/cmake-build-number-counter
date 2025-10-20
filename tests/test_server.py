import json
import urllib.request
import urllib.error

import pytest


def post_json(base_url, path, data):
    """Helper: POST JSON to server, return (status_code, response_dict)."""
    url = f"{base_url}{path}"
    body = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


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
