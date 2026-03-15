import json
import os
import subprocess
import time
import urllib.request
import urllib.error

import pytest

pytestmark = pytest.mark.docker

DOCKER_IMAGE = os.environ.get("CBNC_DOCKER_IMAGE", "cbnc-server:test")


@pytest.fixture(autouse=True)
def require_docker_enabled():
    if not os.environ.get("CBNC_TEST_DOCKER"):
        pytest.skip("Docker tests disabled (set CBNC_TEST_DOCKER=1)")


def docker_run(container_name, data_dir, extra_args=None, port_map="0:8080"):
    """Start a Docker container, return the host URL."""
    cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "-p", port_map,
        "-v", f"{data_dir}:/data",
        DOCKER_IMAGE,
    ]
    if extra_args:
        cmd += extra_args
    else:
        cmd += ["--data-dir", "/data", "--accept-unknown", "--rate-limit", "0"]
    subprocess.run(cmd, check=True, capture_output=True)

    # Wait for container to be running
    deadline = time.time() + 10
    while time.time() < deadline:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container_name],
            capture_output=True, text=True,
        )
        if result.stdout.strip() == "true":
            break
        time.sleep(0.2)
    else:
        logs = subprocess.run(["docker", "logs", container_name], capture_output=True, text=True)
        raise RuntimeError(f"Container {container_name} did not start. Logs:\n{logs.stdout}\n{logs.stderr}")

    result = subprocess.run(
        ["docker", "port", container_name, port_map.split(":")[-1]],
        capture_output=True, text=True, check=True,
    )
    host_port = result.stdout.strip().split(":")[-1]
    return f"http://127.0.0.1:{host_port}"


def docker_rm(container_name):
    """Remove a container and wait until it's fully gone."""
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    deadline = time.time() + 10
    while time.time() < deadline:
        result = subprocess.run(
            ["docker", "inspect", container_name],
            capture_output=True,
        )
        if result.returncode != 0:
            return  # Container gone
        time.sleep(0.2)


def wait_for_server(url, timeout=30):
    """Wait for server to respond to GET /."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{url}/", timeout=1)
            return
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.2)
    raise RuntimeError(f"Server at {url} did not start within {timeout}s")


def post_json(url, path, data):
    """POST JSON and return parsed response."""
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{url}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


@pytest.fixture
def docker_server(tmp_path):
    """Start a Docker container, yield (url, data_dir), cleanup on teardown."""
    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir)
    container_name = f"cbnc-test-{os.getpid()}"

    url = docker_run(container_name, data_dir)
    wait_for_server(url)

    yield url, data_dir

    docker_rm(container_name)


class TestDockerBasic:
    def test_root_endpoint(self, docker_server):
        """GET / returns service info."""
        url, _ = docker_server
        with urllib.request.urlopen(f"{url}/", timeout=5) as resp:
            assert resp.status == 200
            data = json.loads(resp.read())
            assert "service" in data

    def test_increment(self, docker_server):
        """POST /increment twice returns 1 then 2."""
        url, _ = docker_server
        r1 = post_json(url, "/increment", {"project_key": "docker-test"})
        assert r1["build_number"] == 1
        r2 = post_json(url, "/increment", {"project_key": "docker-test"})
        assert r2["build_number"] == 2

    def test_data_persisted_to_volume(self, docker_server):
        """Counter data is visible on the mounted host directory."""
        url, data_dir = docker_server
        post_json(url, "/increment", {"project_key": "persist-test"})

        data_file = os.path.join(data_dir, "build_numbers.json")
        with open(data_file) as f:
            data = json.load(f)
        assert data["persist-test"] == 1


class TestDockerRestart:
    def test_restart_preserves_data(self, tmp_path):
        """Data survives container restart via the same volume."""
        data_dir = str(tmp_path / "data")
        os.makedirs(data_dir)
        name1 = f"cbnc-restart1-{os.getpid()}"
        name2 = f"cbnc-restart2-{os.getpid()}"

        try:
            url1 = docker_run(name1, data_dir)
            wait_for_server(url1)
            r = post_json(url1, "/increment", {"project_key": "restart-test"})
            assert r["build_number"] == 1
            post_json(url1, "/increment", {"project_key": "restart-test"})
        finally:
            docker_rm(name1)

        try:
            url2 = docker_run(name2, data_dir)
            wait_for_server(url2)
            r = post_json(url2, "/increment", {"project_key": "restart-test"})
            assert r["build_number"] == 3
        finally:
            docker_rm(name2)


class TestDockerCustomFlags:
    def test_custom_port(self, tmp_path):
        """Server respects --port flag inside container."""
        data_dir = str(tmp_path / "data")
        os.makedirs(data_dir)
        container_name = f"cbnc-port-{os.getpid()}"

        try:
            url = docker_run(
                container_name, data_dir,
                extra_args=["--data-dir", "/data", "--accept-unknown",
                            "--rate-limit", "0", "--port", "9090"],
                port_map="0:9090",
            )
            wait_for_server(url)
            with urllib.request.urlopen(f"{url}/", timeout=5) as resp:
                assert resp.status == 200
        finally:
            docker_rm(container_name)
