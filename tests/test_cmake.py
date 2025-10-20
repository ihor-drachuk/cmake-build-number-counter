import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
EXAMPLE_SIMPLE = PROJECT_ROOT / "examples" / "1-simple"


@pytest.mark.cmake
def test_example_simple_builds(tmp_path):
    """Configure and build example-1-simple, verify version.h is generated."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()

    # Configure
    result = subprocess.run(
        ["cmake", str(EXAMPLE_SIMPLE)],
        cwd=str(build_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"CMake configure failed:\n{result.stderr}"

    # Build (this triggers version header generation)
    result = subprocess.run(
        ["cmake", "--build", "."],
        cwd=str(build_dir),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"CMake build failed:\n{result.stderr}"

    # Verify version.h exists and has correct content
    version_h = build_dir / "version.h"
    assert version_h.exists(), "version.h was not generated"

    content = version_h.read_text()
    assert "APP_VERSION_MAJOR 2" in content
    assert "APP_VERSION_MINOR 1" in content
    assert "APP_VERSION_PATCH 3" in content
    assert "APP_VERSION_BUILD" in content
    assert "APP_VERSION_STRING" in content


@pytest.mark.cmake
def test_build_number_increments(tmp_path):
    """Build twice, verify build number increments."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()

    # Configure
    subprocess.run(
        ["cmake", str(EXAMPLE_SIMPLE)],
        cwd=str(build_dir),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )

    # First build
    subprocess.run(
        ["cmake", "--build", "."],
        cwd=str(build_dir),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    content1 = (build_dir / "version.h").read_text()

    # Second build
    subprocess.run(
        ["cmake", "--build", "."],
        cwd=str(build_dir),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    content2 = (build_dir / "version.h").read_text()

    # Extract build numbers
    def extract_build(text):
        for line in text.splitlines():
            if "APP_VERSION_BUILD" in line and "#define" in line:
                return int(line.split()[-1])
        raise ValueError("APP_VERSION_BUILD not found")

    build1 = extract_build(content1)
    build2 = extract_build(content2)
    assert build2 == build1 + 1, f"Build number did not increment: {build1} -> {build2}"
