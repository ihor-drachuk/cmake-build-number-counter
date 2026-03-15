import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
EXAMPLE_SIMPLE = PROJECT_ROOT / "examples" / "1-simple"
EXAMPLE_CONFIGURE = PROJECT_ROOT / "examples" / "4-configure-mode"
SRC_DIR = PROJECT_ROOT / "src"


def cmake_configure(source_dir, build_dir, timeout=30, env=None):
    """Run cmake configure and return the result."""
    run_env = None
    if env:
        run_env = {**os.environ, **env}
    return subprocess.run(
        ["cmake", str(source_dir)],
        cwd=str(build_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=run_env,
    )


def cmake_build(build_dir, timeout=60, env=None):
    """Run cmake build and return the result."""
    run_env = None
    if env:
        run_env = {**os.environ, **env}
    return subprocess.run(
        ["cmake", "--build", "."],
        cwd=str(build_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=run_env,
    )


def extract_build_number_from_header(text):
    """Extract APP_VERSION_BUILD value from version.h content."""
    for line in text.splitlines():
        if "APP_VERSION_BUILD" in line and "#define" in line:
            return int(line.split()[-1])
    raise ValueError("APP_VERSION_BUILD not found")


def extract_build_number_from_cmake_output(output):
    """Extract build number from cmake configure output (status messages)."""
    # Look for "Build number for '...': N"
    match = re.search(r"Build number for '[^']+': (\d+)", output)
    if match:
        return int(match.group(1))
    # Also check combined stdout+stderr
    raise ValueError(f"Build number not found in cmake output")


def write_temp_cmakelists(tmp_path, content):
    """Write a CMakeLists.txt to tmp_path and return source dir."""
    source_dir = tmp_path / "source"
    source_dir.mkdir(exist_ok=True)
    (source_dir / "CMakeLists.txt").write_text(content)
    return source_dir


# ============================================================
# BUILD mode tests (existing)
# ============================================================

@pytest.mark.cmake
def test_example_simple_builds(tmp_path):
    """Configure and build example-1-simple, verify version.h is generated."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()

    # Configure
    result = cmake_configure(EXAMPLE_SIMPLE, build_dir)
    assert result.returncode == 0, f"CMake configure failed:\n{result.stderr}"

    # Build (this triggers version header generation)
    result = cmake_build(build_dir)
    assert result.returncode == 0, f"CMake build failed:\n{result.stderr}"

    # Verify version.h exists and has correct content
    version_h = build_dir / "generated" / "version.h"
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
    cmake_configure(EXAMPLE_SIMPLE, build_dir)

    # First build
    cmake_build(build_dir)
    content1 = (build_dir / "generated" / "version.h").read_text()

    # Second build
    cmake_build(build_dir)
    content2 = (build_dir / "generated" / "version.h").read_text()

    build1 = extract_build_number_from_header(content1)
    build2 = extract_build_number_from_header(content2)
    assert build2 == build1 + 1, f"Build number did not increment: {build1} -> {build2}"


# ============================================================
# CONFIGURE mode tests
# ============================================================

@pytest.mark.cmake
def test_configure_mode_builds(tmp_path):
    """Configure + build example-4-configure-mode successfully."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()

    # Configure
    result = cmake_configure(EXAMPLE_CONFIGURE, build_dir)
    assert result.returncode == 0, f"CMake configure failed:\n{result.stderr}"

    # Verify build number appears in configure output
    combined = result.stdout + result.stderr
    assert "Build number for 'example-configure'" in combined

    # Build
    result = cmake_build(build_dir)
    assert result.returncode == 0, f"CMake build failed:\n{result.stderr}"


@pytest.mark.cmake
def test_configure_mode_increments_on_reconfigure(tmp_path):
    """Configure twice, verify build number increments."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()

    # First configure
    result1 = cmake_configure(EXAMPLE_CONFIGURE, build_dir)
    assert result1.returncode == 0, f"First configure failed:\n{result1.stderr}"
    build1 = extract_build_number_from_cmake_output(result1.stdout + result1.stderr)

    # Delete CMakeCache to force re-configure, but keep build_number.txt
    cache_file = build_dir / "CMakeCache.txt"
    if cache_file.exists():
        cache_file.unlink()

    # Second configure
    result2 = cmake_configure(EXAMPLE_CONFIGURE, build_dir)
    assert result2.returncode == 0, f"Second configure failed:\n{result2.stderr}"
    build2 = extract_build_number_from_cmake_output(result2.stdout + result2.stderr)

    assert build2 == build1 + 1, f"Build number did not increment on reconfigure: {build1} -> {build2}"


@pytest.mark.cmake
def test_configure_mode_output_variable_before_project(tmp_path):
    """OUTPUT_VARIABLE works when called before project()."""
    source_dir = write_temp_cmakelists(tmp_path, f"""
cmake_minimum_required(VERSION 3.20)
list(APPEND CMAKE_MODULE_PATH "{SRC_DIR.as_posix()}")
include(CMakeBuildNumber)

increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "test-output-var"
    OUTPUT_VARIABLE MY_BUILD_NUM
    QUIET
)

message(STATUS "GOT_BUILD_NUM=${{MY_BUILD_NUM}}")
project(TestOutputVar LANGUAGES NONE)
""")

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    result = cmake_configure(source_dir, build_dir)
    assert result.returncode == 0, f"Configure failed:\n{result.stderr}"

    combined = result.stdout + result.stderr
    match = re.search(r"GOT_BUILD_NUM=(\d+)", combined)
    assert match, f"OUTPUT_VARIABLE not set. Output:\n{combined}"
    assert int(match.group(1)) > 0


@pytest.mark.cmake
def test_configure_mode_header_requires_project_version(tmp_path):
    """VERSION_HEADER before project() should fail."""
    source_dir = write_temp_cmakelists(tmp_path, f"""
cmake_minimum_required(VERSION 3.20)
list(APPEND CMAKE_MODULE_PATH "{SRC_DIR.as_posix()}")
include(CMakeBuildNumber)

increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "test-header-fail"
    VERSION_HEADER "${{CMAKE_BINARY_DIR}}/generated/version.h"
    QUIET
)

project(TestHeaderFail LANGUAGES NONE)
""")

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    result = cmake_configure(source_dir, build_dir)
    assert result.returncode != 0, "Expected configure to fail"
    assert "PROJECT_VERSION" in result.stderr


# ============================================================
# NO_INCREMENT tests
# ============================================================

@pytest.mark.cmake
def test_no_increment_reads_current_value(tmp_path):
    """NO_INCREMENT reads current counter without incrementing across separate configures."""
    # CMakeLists that increments normally
    increment_cmake = f"""
cmake_minimum_required(VERSION 3.20)
list(APPEND CMAKE_MODULE_PATH "{SRC_DIR.as_posix()}")
include(CMakeBuildNumber)

increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "test-no-incr"
    OUTPUT_VARIABLE NUM
    QUIET
)
message(STATUS "VALUE=${{NUM}}")
project(TestNoIncrement LANGUAGES NONE)
"""
    # CMakeLists that reads with NO_INCREMENT
    no_incr_cmake = f"""
cmake_minimum_required(VERSION 3.20)
list(APPEND CMAKE_MODULE_PATH "{SRC_DIR.as_posix()}")
include(CMakeBuildNumber)

increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "test-no-incr"
    OUTPUT_VARIABLE NUM
    NO_INCREMENT
    QUIET
)
message(STATUS "VALUE=${{NUM}}")
project(TestNoIncrement LANGUAGES NONE)
"""

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    build_dir = tmp_path / "build"
    build_dir.mkdir()

    def configure_and_get_value(cmake_content):
        (source_dir / "CMakeLists.txt").write_text(cmake_content)
        # Delete CMakeCache to force full re-configure
        cache = build_dir / "CMakeCache.txt"
        if cache.exists():
            cache.unlink()
        result = cmake_configure(source_dir, build_dir)
        assert result.returncode == 0, f"Configure failed:\n{result.stderr}"
        match = re.search(r"VALUE=(\d+)", result.stdout + result.stderr)
        assert match, f"VALUE not found in output"
        return int(match.group(1))

    # 1st configure: normal increment → 1
    val1 = configure_and_get_value(increment_cmake)

    # 2nd configure: NO_INCREMENT → still 1 (no change)
    val2 = configure_and_get_value(no_incr_cmake)
    assert val2 == val1, f"NO_INCREMENT should not change value: {val1} -> {val2}"

    # 3rd configure: normal increment → 2
    val3 = configure_and_get_value(increment_cmake)
    assert val3 == val1 + 1, f"Increment after NO_INCREMENT should continue: {val1} -> {val3}"

    # 4th configure: normal increment → 3
    val4 = configure_and_get_value(increment_cmake)
    assert val4 == val3 + 1, f"Second increment should continue: {val3} -> {val4}"


@pytest.mark.cmake
def test_no_increment_fails_without_prior_counter(tmp_path):
    """NO_INCREMENT without existing counter file should fail."""
    source_dir = write_temp_cmakelists(tmp_path, f"""
cmake_minimum_required(VERSION 3.20)
list(APPEND CMAKE_MODULE_PATH "{SRC_DIR.as_posix()}")
include(CMakeBuildNumber)

increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "test-no-incr-fail"
    OUTPUT_VARIABLE MY_NUM
    NO_INCREMENT
    QUIET
)

project(TestNoIncrFail LANGUAGES NONE)
""")

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    result = cmake_configure(source_dir, build_dir)
    assert result.returncode != 0, "Expected configure to fail"
    assert "NO_INCREMENT" in result.stderr


@pytest.mark.cmake
def test_no_increment_with_force_version_fails(tmp_path):
    """NO_INCREMENT + FORCE_VERSION should fail (mutually exclusive)."""
    source_dir = write_temp_cmakelists(tmp_path, f"""
cmake_minimum_required(VERSION 3.20)
list(APPEND CMAKE_MODULE_PATH "{SRC_DIR.as_posix()}")
include(CMakeBuildNumber)

increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "test-mutex"
    OUTPUT_VARIABLE MY_NUM
    NO_INCREMENT
    FORCE_VERSION 5
    QUIET
)

project(TestMutex LANGUAGES NONE)
""")

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    result = cmake_configure(source_dir, build_dir)
    assert result.returncode != 0, "Expected configure to fail"
    assert "mutually exclusive" in result.stderr.lower() or "NO_INCREMENT" in result.stderr


# ============================================================
# Auto-reconfigure tests
# ============================================================

@pytest.mark.cmake
def test_configure_mode_auto_reconfigure_on_build(tmp_path):
    """In CONFIGURE mode, cmake --build should trigger reconfigure and increment."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()

    counter_file = build_dir / "build_number.txt"

    # Configure
    result = cmake_configure(EXAMPLE_CONFIGURE, build_dir)
    assert result.returncode == 0, f"Configure failed:\n{result.stderr}"
    assert counter_file.exists(), "Counter file should exist after configure"
    build1 = int(counter_file.read_text().strip())

    # Build (should trigger reconfigure due to phantom file dependency)
    result = cmake_build(build_dir, timeout=120)
    assert result.returncode == 0, f"First build failed:\n{result.stderr}"
    build2 = int(counter_file.read_text().strip())
    assert build2 == build1 + 1, \
        f"Auto-reconfigure should have incremented: {build1} -> {build2}"

    # Second build — should increment again
    result = cmake_build(build_dir, timeout=120)
    assert result.returncode == 0, f"Second build failed:\n{result.stderr}"
    build3 = int(counter_file.read_text().strip())
    assert build3 == build2 + 1, \
        f"Second auto-reconfigure should have incremented: {build2} -> {build3}"


@pytest.mark.cmake
def test_build_mode_no_auto_reconfigure(tmp_path):
    """BUILD mode increments on every build WITHOUT triggering reconfigure."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()

    version_h = build_dir / "generated" / "version.h"

    # Configure
    result = cmake_configure(EXAMPLE_SIMPLE, build_dir)
    assert result.returncode == 0, f"Configure failed:\n{result.stderr}"

    # First build
    result = cmake_build(build_dir)
    assert result.returncode == 0, f"First build failed:\n{result.stderr}"
    build1 = extract_build_number_from_header(version_h.read_text())

    # Second build — should NOT reconfigure but SHOULD increment
    result = cmake_build(build_dir)
    assert result.returncode == 0, f"Second build failed:\n{result.stderr}"
    build2 = extract_build_number_from_header(version_h.read_text())

    # Verify no reconfigure happened
    combined = result.stdout + result.stderr
    assert "Configuring done" not in combined, \
        "BUILD mode should not trigger auto-reconfigure"

    # But build number DID increment (via custom target, not reconfigure)
    assert build2 == build1 + 1, \
        f"BUILD mode should increment without reconfigure: {build1} -> {build2}"


# ============================================================
# CONFIGURE mode + VERSION_HEADER (after project)
# ============================================================

@pytest.mark.cmake
def test_configure_mode_version_header_after_project(tmp_path):
    """VERSION_HEADER in CONFIGURE mode works after project()."""
    source_dir = write_temp_cmakelists(tmp_path, f"""
cmake_minimum_required(VERSION 3.20)
list(APPEND CMAKE_MODULE_PATH "{SRC_DIR.as_posix()}")
include(CMakeBuildNumber)

increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "test-header-ok"
    OUTPUT_VARIABLE BUILD_NUM
    QUIET
)

project(TestHeaderOk VERSION 2.0.0.${{BUILD_NUM}} LANGUAGES NONE)

increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "test-header-ok"
    VERSION_HEADER "${{CMAKE_BINARY_DIR}}/generated/version.h"
    NO_INCREMENT
    QUIET
)
""")

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    result = cmake_configure(source_dir, build_dir)
    assert result.returncode == 0, f"Configure failed:\n{result.stderr}"

    version_h = build_dir / "generated" / "version.h"
    assert version_h.exists(), "version.h was not generated"

    content = version_h.read_text()
    assert "APP_VERSION_MAJOR 2" in content
    assert "APP_VERSION_MINOR 0" in content
    assert "APP_VERSION_PATCH 0" in content
    assert "APP_VERSION_BUILD" in content

    # Build number in header should match OUTPUT_VARIABLE
    build_num = extract_build_number_from_header(content)
    assert build_num > 0


# ============================================================
# CONFIGURE mode + FORCE_VERSION
# ============================================================

@pytest.mark.cmake
def test_configure_mode_force_version(tmp_path):
    """FORCE_VERSION sets exact value, subsequent separate configures increment from it."""
    force_cmake = f"""
cmake_minimum_required(VERSION 3.20)
list(APPEND CMAKE_MODULE_PATH "{SRC_DIR.as_posix()}")
include(CMakeBuildNumber)

increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "test-force"
    OUTPUT_VARIABLE NUM
    FORCE_VERSION 99
    QUIET
)
message(STATUS "VALUE=${{NUM}}")
project(TestForce LANGUAGES NONE)
"""
    increment_cmake = f"""
cmake_minimum_required(VERSION 3.20)
list(APPEND CMAKE_MODULE_PATH "{SRC_DIR.as_posix()}")
include(CMakeBuildNumber)

increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "test-force"
    OUTPUT_VARIABLE NUM
    QUIET
)
message(STATUS "VALUE=${{NUM}}")
project(TestForce LANGUAGES NONE)
"""

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    build_dir = tmp_path / "build"
    build_dir.mkdir()

    def configure_and_get_value(cmake_content):
        (source_dir / "CMakeLists.txt").write_text(cmake_content)
        cache = build_dir / "CMakeCache.txt"
        if cache.exists():
            cache.unlink()
        result = cmake_configure(source_dir, build_dir)
        assert result.returncode == 0, f"Configure failed:\n{result.stderr}"
        match = re.search(r"VALUE=(\d+)", result.stdout + result.stderr)
        assert match, f"VALUE not found in output"
        return int(match.group(1))

    # Force to 99
    val1 = configure_and_get_value(force_cmake)
    assert val1 == 99

    # First increment after force → 100
    val2 = configure_and_get_value(increment_cmake)
    assert val2 == 100, f"First increment after FORCE_VERSION 99 should be 100, got {val2}"

    # Second increment → 101
    val3 = configure_and_get_value(increment_cmake)
    assert val3 == 101, f"Second increment should be 101, got {val3}"


# ============================================================
# Invalid MODE
# ============================================================

@pytest.mark.cmake
def test_invalid_mode_fails(tmp_path):
    """Invalid MODE value should produce FATAL_ERROR."""
    source_dir = write_temp_cmakelists(tmp_path, f"""
cmake_minimum_required(VERSION 3.20)
list(APPEND CMAKE_MODULE_PATH "{SRC_DIR.as_posix()}")
include(CMakeBuildNumber)

increment_build_number(
    MODE INVALID
    PROJECT_KEY "test-invalid-mode"
    OUTPUT_VARIABLE MY_NUM
)

project(TestInvalid LANGUAGES NONE)
""")

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    result = cmake_configure(source_dir, build_dir)
    assert result.returncode != 0, "Expected configure to fail"
    assert "MODE must be BUILD or CONFIGURE" in result.stderr


# ============================================================
# BUILD mode + NO_INCREMENT
# ============================================================

@pytest.mark.cmake
def test_build_mode_no_increment(tmp_path):
    """NO_INCREMENT in BUILD mode reads counter without incrementing."""
    source_dir = write_temp_cmakelists(tmp_path, f"""
cmake_minimum_required(VERSION 3.20)
list(APPEND CMAKE_MODULE_PATH "{SRC_DIR.as_posix()}")
include(CMakeBuildNumber)

project(TestBuildNoIncr VERSION 1.0.0.0 LANGUAGES CXX)

increment_build_number(
    PROJECT_KEY "test-build-no-incr"
    VERSION_HEADER "${{CMAKE_BINARY_DIR}}/generated/version.h"
    FORCE_VERSION 7
    QUIET
)
""")

    # Write a minimal main.cpp
    (source_dir / "main.cpp").write_text("int main() { return 0; }\n")

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    # Configure + build to set counter to 7
    result = cmake_configure(source_dir, build_dir)
    assert result.returncode == 0, f"Configure failed:\n{result.stderr}"
    result = cmake_build(build_dir)
    assert result.returncode == 0, f"Build failed:\n{result.stderr}"

    content1 = (build_dir / "generated" / "version.h").read_text()
    build1 = extract_build_number_from_header(content1)
    assert build1 == 7

    # Now switch to NO_INCREMENT — reconfigure with new CMakeLists
    (source_dir / "CMakeLists.txt").write_text(f"""
cmake_minimum_required(VERSION 3.20)
list(APPEND CMAKE_MODULE_PATH "{SRC_DIR.as_posix()}")
include(CMakeBuildNumber)

project(TestBuildNoIncr VERSION 1.0.0.0 LANGUAGES CXX)

increment_build_number(
    PROJECT_KEY "test-build-no-incr"
    VERSION_HEADER "${{CMAKE_BINARY_DIR}}/generated/version.h"
    NO_INCREMENT
    QUIET
)

add_executable(test_app main.cpp)
""")

    result = cmake_configure(source_dir, build_dir)
    assert result.returncode == 0, f"Reconfigure failed:\n{result.stderr}"
    result = cmake_build(build_dir)
    assert result.returncode == 0, f"Build with NO_INCREMENT failed:\n{result.stderr}"

    content2 = (build_dir / "generated" / "version.h").read_text()
    build2 = extract_build_number_from_header(content2)
    assert build2 == 7, f"NO_INCREMENT should keep value at 7, got {build2}"


# ============================================================
# FetchContent tests
# ============================================================

@pytest.mark.cmake
def test_fetchcontent_build_mode(tmp_path):
    """FetchContent integration works in BUILD mode with correct increments."""
    repo_url = PROJECT_ROOT.as_posix()
    source_dir = write_temp_cmakelists(tmp_path, f"""
cmake_minimum_required(VERSION 3.20)

include(FetchContent)
FetchContent_Declare(
    build_number_counter
    SOURCE_DIR "{repo_url}"
)
FetchContent_MakeAvailable(build_number_counter)

project(TestFetchBuild VERSION 1.0.0.0 LANGUAGES CXX)

list(APPEND CMAKE_MODULE_PATH "${{build_number_counter_SOURCE_DIR}}/src")
include(CMakeBuildNumber)

increment_build_number(
    PROJECT_KEY "test-fetch-build"
    VERSION_HEADER "${{CMAKE_BINARY_DIR}}/generated/version.h"
    QUIET
)

add_executable(test_app main.cpp)
target_include_directories(test_app PRIVATE ${{CMAKE_BINARY_DIR}}/generated)
add_dependencies(test_app generate_version_test-fetch-build)
""")

    (source_dir / "main.cpp").write_text('#include "version.h"\nint main() { return 0; }\n')

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    result = cmake_configure(source_dir, build_dir, timeout=60)
    assert result.returncode == 0, f"Configure failed:\n{result.stderr}"

    # First build
    result = cmake_build(build_dir)
    assert result.returncode == 0, f"First build failed:\n{result.stderr}"
    build1 = extract_build_number_from_header((build_dir / "generated" / "version.h").read_text())
    assert build1 > 0

    # Second build — must increment
    result = cmake_build(build_dir)
    assert result.returncode == 0, f"Second build failed:\n{result.stderr}"
    build2 = extract_build_number_from_header((build_dir / "generated" / "version.h").read_text())
    assert build2 == build1 + 1, f"FetchContent BUILD mode did not increment: {build1} -> {build2}"


@pytest.mark.cmake
def test_fetchcontent_configure_mode(tmp_path):
    """FetchContent integration works in CONFIGURE mode with auto-reconfigure increments."""
    repo_url = PROJECT_ROOT.as_posix()
    source_dir = write_temp_cmakelists(tmp_path, f"""
cmake_minimum_required(VERSION 3.20)

include(FetchContent)
FetchContent_Declare(
    build_number_counter
    SOURCE_DIR "{repo_url}"
)
FetchContent_MakeAvailable(build_number_counter)

list(APPEND CMAKE_MODULE_PATH "${{build_number_counter_SOURCE_DIR}}/src")
include(CMakeBuildNumber)

increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "test-fetch-configure"
    OUTPUT_VARIABLE BUILD_NUM
    QUIET
)

message(STATUS "FETCH_BUILD_NUM=${{BUILD_NUM}}")
project(TestFetchConfigure VERSION 1.0.0.${{BUILD_NUM}} LANGUAGES CXX)

add_executable(test_app main.cpp)
target_compile_definitions(test_app PRIVATE APP_BUILD=${{PROJECT_VERSION_TWEAK}})
""")

    (source_dir / "main.cpp").write_text("int main() { return 0; }\n")

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    counter_file = build_dir / "build_number.txt"

    result = cmake_configure(source_dir, build_dir, timeout=60)
    assert result.returncode == 0, f"Configure failed:\n{result.stderr}"
    build1 = int(counter_file.read_text().strip())
    assert build1 > 0

    # First build — should auto-reconfigure and increment
    result = cmake_build(build_dir, timeout=120)
    assert result.returncode == 0, f"First build failed:\n{result.stderr}"
    build2 = int(counter_file.read_text().strip())
    assert build2 == build1 + 1, f"FetchContent CONFIGURE mode did not increment: {build1} -> {build2}"

    # Second build — should increment again
    result = cmake_build(build_dir, timeout=120)
    assert result.returncode == 0, f"Second build failed:\n{result.stderr}"
    build3 = int(counter_file.read_text().strip())
    assert build3 == build2 + 1, f"Second increment failed: {build2} -> {build3}"


# ============================================================
# Server integration tests
# ============================================================

def _build_mode_cmakelists(server_url=None, server_token=None, project_key="test-server"):
    """Generate a BUILD mode CMakeLists.txt with optional server params."""
    server_line = ""
    if server_url:
        server_line += f'    SERVER_URL "{server_url}"\n'
    if server_token:
        server_line += f'    SERVER_TOKEN "{server_token}"\n'

    return f"""
cmake_minimum_required(VERSION 3.20)
list(APPEND CMAKE_MODULE_PATH "{SRC_DIR.as_posix()}")
include(CMakeBuildNumber)

project(TestServer VERSION 1.0.0.0 LANGUAGES CXX)

increment_build_number(
    PROJECT_KEY "{project_key}"
    VERSION_HEADER "${{CMAKE_BINARY_DIR}}/generated/version.h"
{server_line}    QUIET
)

add_executable(test_app main.cpp)
target_include_directories(test_app PRIVATE ${{CMAKE_BINARY_DIR}}/generated)
add_dependencies(test_app generate_version_{project_key})
"""


def _configure_mode_cmakelists(server_url=None, server_token=None, project_key="test-server"):
    """Generate a CONFIGURE mode CMakeLists.txt with optional server params."""
    server_line = ""
    if server_url:
        server_line += f'    SERVER_URL "{server_url}"\n'
    if server_token:
        server_line += f'    SERVER_TOKEN "{server_token}"\n'

    return f"""
cmake_minimum_required(VERSION 3.20)
list(APPEND CMAKE_MODULE_PATH "{SRC_DIR.as_posix()}")
include(CMakeBuildNumber)

increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "{project_key}"
    OUTPUT_VARIABLE BUILD_NUM
{server_line}    QUIET
)

message(STATUS "VALUE=${{BUILD_NUM}}")
project(TestServer LANGUAGES NONE)
"""


# --- Basic server (no auth) ---

@pytest.mark.cmake
def test_server_build_mode_cmake_params(tmp_path, running_server):
    """BUILD mode with SERVER_URL passed via CMake parameter."""
    source_dir = write_temp_cmakelists(
        tmp_path, _build_mode_cmakelists(server_url=running_server))
    (source_dir / "main.cpp").write_text('#include "version.h"\nint main() { return 0; }\n')

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    result = cmake_configure(source_dir, build_dir)
    assert result.returncode == 0, f"Configure failed:\n{result.stderr}"

    result = cmake_build(build_dir)
    assert result.returncode == 0, f"First build failed:\n{result.stderr}"
    build1 = extract_build_number_from_header((build_dir / "generated" / "version.h").read_text())

    result = cmake_build(build_dir)
    assert result.returncode == 0, f"Second build failed:\n{result.stderr}"
    build2 = extract_build_number_from_header((build_dir / "generated" / "version.h").read_text())

    assert build2 == build1 + 1, f"Server increment failed: {build1} -> {build2}"


@pytest.mark.cmake
def test_server_build_mode_env_vars(tmp_path, running_server):
    """BUILD mode with server URL via BUILD_SERVER_URL env var."""
    source_dir = write_temp_cmakelists(
        tmp_path, _build_mode_cmakelists())  # no server params in CMake
    (source_dir / "main.cpp").write_text('#include "version.h"\nint main() { return 0; }\n')

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    env = {"BUILD_SERVER_URL": running_server}

    result = cmake_configure(source_dir, build_dir, env=env)
    assert result.returncode == 0, f"Configure failed:\n{result.stderr}"

    result = cmake_build(build_dir, env=env)
    assert result.returncode == 0, f"First build failed:\n{result.stderr}"
    build1 = extract_build_number_from_header((build_dir / "generated" / "version.h").read_text())

    result = cmake_build(build_dir, env=env)
    assert result.returncode == 0, f"Second build failed:\n{result.stderr}"
    build2 = extract_build_number_from_header((build_dir / "generated" / "version.h").read_text())

    assert build2 == build1 + 1, f"Server increment via env var failed: {build1} -> {build2}"


@pytest.mark.cmake
def test_server_configure_mode_cmake_params(tmp_path, running_server):
    """CONFIGURE mode with SERVER_URL via CMake parameter."""
    source_dir = write_temp_cmakelists(
        tmp_path, _configure_mode_cmakelists(server_url=running_server))

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    result1 = cmake_configure(source_dir, build_dir)
    assert result1.returncode == 0, f"First configure failed:\n{result1.stderr}"
    val1 = int(re.search(r"VALUE=(\d+)", result1.stdout + result1.stderr).group(1))

    cache = build_dir / "CMakeCache.txt"
    if cache.exists():
        cache.unlink()

    result2 = cmake_configure(source_dir, build_dir)
    assert result2.returncode == 0, f"Second configure failed:\n{result2.stderr}"
    val2 = int(re.search(r"VALUE=(\d+)", result2.stdout + result2.stderr).group(1))

    assert val2 == val1 + 1, f"Server configure increment failed: {val1} -> {val2}"


@pytest.mark.cmake
def test_server_configure_mode_env_vars(tmp_path, running_server):
    """CONFIGURE mode with server URL via BUILD_SERVER_URL env var."""
    source_dir = write_temp_cmakelists(
        tmp_path, _configure_mode_cmakelists())  # no server params
    env = {"BUILD_SERVER_URL": running_server}

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    result1 = cmake_configure(source_dir, build_dir, env=env)
    assert result1.returncode == 0, f"First configure failed:\n{result1.stderr}"
    val1 = int(re.search(r"VALUE=(\d+)", result1.stdout + result1.stderr).group(1))

    cache = build_dir / "CMakeCache.txt"
    if cache.exists():
        cache.unlink()

    result2 = cmake_configure(source_dir, build_dir, env=env)
    assert result2.returncode == 0, f"Second configure failed:\n{result2.stderr}"
    val2 = int(re.search(r"VALUE=(\d+)", result2.stdout + result2.stderr).group(1))

    assert val2 == val1 + 1, f"Server configure increment via env var failed: {val1} -> {val2}"


# --- Auth: valid tokens ---

@pytest.mark.cmake
def test_server_auth_project_token(tmp_path, cmake_auth_server):
    """BUILD mode with exact project-scoped token."""
    url = cmake_auth_server['url']
    token = cmake_auth_server['project_token']
    source_dir = write_temp_cmakelists(
        tmp_path, _build_mode_cmakelists(
            server_url=url, server_token=token, project_key="cmake-test-project"))
    (source_dir / "main.cpp").write_text('#include "version.h"\nint main() { return 0; }\n')

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    result = cmake_configure(source_dir, build_dir)
    assert result.returncode == 0, f"Configure failed:\n{result.stderr}"

    result = cmake_build(build_dir)
    assert result.returncode == 0, f"First build failed:\n{result.stderr}"
    build1 = extract_build_number_from_header((build_dir / "generated" / "version.h").read_text())

    result = cmake_build(build_dir)
    assert result.returncode == 0, f"Second build failed:\n{result.stderr}"
    build2 = extract_build_number_from_header((build_dir / "generated" / "version.h").read_text())

    assert build2 == build1 + 1, f"Auth project token increment failed: {build1} -> {build2}"


@pytest.mark.cmake
def test_server_auth_wildcard_token(tmp_path, cmake_auth_server):
    """BUILD mode with wildcard token matching cmake-test-*."""
    url = cmake_auth_server['url']
    token = cmake_auth_server['wildcard_token']
    source_dir = write_temp_cmakelists(
        tmp_path, _build_mode_cmakelists(
            server_url=url, server_token=token, project_key="cmake-test-wildcard-proj"))
    (source_dir / "main.cpp").write_text('#include "version.h"\nint main() { return 0; }\n')

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    result = cmake_configure(source_dir, build_dir)
    assert result.returncode == 0, f"Configure failed:\n{result.stderr}"

    result = cmake_build(build_dir)
    assert result.returncode == 0, f"First build failed:\n{result.stderr}"
    build1 = extract_build_number_from_header((build_dir / "generated" / "version.h").read_text())

    result = cmake_build(build_dir)
    assert result.returncode == 0, f"Second build failed:\n{result.stderr}"
    build2 = extract_build_number_from_header((build_dir / "generated" / "version.h").read_text())

    assert build2 == build1 + 1, f"Auth wildcard token increment failed: {build1} -> {build2}"


@pytest.mark.cmake
def test_server_auth_admin_token(tmp_path, cmake_auth_server):
    """BUILD mode with admin token (access to any project)."""
    url = cmake_auth_server['url']
    token = cmake_auth_server['admin_token']
    source_dir = write_temp_cmakelists(
        tmp_path, _build_mode_cmakelists(
            server_url=url, server_token=token, project_key="any-project-name"))
    (source_dir / "main.cpp").write_text('#include "version.h"\nint main() { return 0; }\n')

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    result = cmake_configure(source_dir, build_dir)
    assert result.returncode == 0, f"Configure failed:\n{result.stderr}"

    result = cmake_build(build_dir)
    assert result.returncode == 0, f"First build failed:\n{result.stderr}"
    build1 = extract_build_number_from_header((build_dir / "generated" / "version.h").read_text())

    result = cmake_build(build_dir)
    assert result.returncode == 0, f"Second build failed:\n{result.stderr}"
    build2 = extract_build_number_from_header((build_dir / "generated" / "version.h").read_text())

    assert build2 == build1 + 1, f"Auth admin token increment failed: {build1} -> {build2}"


@pytest.mark.cmake
def test_server_auth_env_var_token(tmp_path, cmake_auth_server):
    """Token passed via BUILD_SERVER_TOKEN env var."""
    url = cmake_auth_server['url']
    token = cmake_auth_server['project_token']
    source_dir = write_temp_cmakelists(
        tmp_path, _build_mode_cmakelists(
            server_url=url, project_key="cmake-test-project"))  # no token in CMake
    (source_dir / "main.cpp").write_text('#include "version.h"\nint main() { return 0; }\n')

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    env = {"BUILD_SERVER_TOKEN": token}

    result = cmake_configure(source_dir, build_dir, env=env)
    assert result.returncode == 0, f"Configure failed:\n{result.stderr}"

    result = cmake_build(build_dir, env=env)
    assert result.returncode == 0, f"First build failed:\n{result.stderr}"
    build1 = extract_build_number_from_header((build_dir / "generated" / "version.h").read_text())

    result = cmake_build(build_dir, env=env)
    assert result.returncode == 0, f"Second build failed:\n{result.stderr}"
    build2 = extract_build_number_from_header((build_dir / "generated" / "version.h").read_text())

    assert build2 == build1 + 1, f"Auth env var token increment failed: {build1} -> {build2}"


# --- Auth: rejection and fallback ---

@pytest.mark.cmake
def test_server_auth_wrong_token_fails_build(tmp_path, cmake_auth_server):
    """Wrong token causes server rejection (401); build fails instead of silent fallback."""
    url = cmake_auth_server['url']
    source_dir = write_temp_cmakelists(
        tmp_path, _build_mode_cmakelists(
            server_url=url, server_token="invalid-token", project_key="cmake-test-project"))
    (source_dir / "main.cpp").write_text('#include "version.h"\nint main() { return 0; }\n')

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    result = cmake_configure(source_dir, build_dir)
    assert result.returncode == 0, f"Configure failed:\n{result.stderr}"

    result = cmake_build(build_dir)
    assert result.returncode != 0, "Build should fail on auth rejection"
    combined = result.stdout + result.stderr
    assert "Server rejected" in combined or "Invalid token" in combined


@pytest.mark.cmake
def test_no_increment_ignores_server_changes(tmp_path, running_server):
    """NO_INCREMENT reads local file even when server has a newer value.

    Simulates the documented pattern: increment before project(), then
    NO_INCREMENT after project() to generate VERSION_HEADER. Between the
    two calls another client increments the same key on the server.
    NO_INCREMENT must return the original local value, not the server's.
    """
    project_key = "test-no-incr-server"

    # CMakeLists: increment + NO_INCREMENT in one configure (the documented pattern)
    source_dir = write_temp_cmakelists(tmp_path, f"""
cmake_minimum_required(VERSION 3.20)
list(APPEND CMAKE_MODULE_PATH "{SRC_DIR.as_posix()}")
include(CMakeBuildNumber)

# Step 1: normal increment (talks to server)
increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "{project_key}"
    OUTPUT_VARIABLE BUILD_NUM
    SERVER_URL "{running_server}"
    QUIET
)

message(STATUS "STEP1=${{BUILD_NUM}}")
project(TestNoIncrServer VERSION 1.0.0.${{BUILD_NUM}} LANGUAGES NONE)

# Step 2: NO_INCREMENT — must reuse local value, no server call
increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "{project_key}"
    VERSION_HEADER "${{CMAKE_BINARY_DIR}}/generated/version.h"
    NO_INCREMENT
    QUIET
)
""")

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    # First configure — establishes counter on server (e.g. 1)
    result = cmake_configure(source_dir, build_dir)
    assert result.returncode == 0, f"First configure failed:\n{result.stderr}"
    combined = result.stdout + result.stderr
    match = re.search(r"STEP1=(\d+)", combined)
    assert match, f"STEP1 not found in output:\n{combined}"
    first_num = int(match.group(1))

    version_h = build_dir / "generated" / "version.h"
    assert version_h.exists(), "version.h was not generated"
    header_num = extract_build_number_from_header(version_h.read_text())
    assert header_num == first_num, (
        f"NO_INCREMENT header ({header_num}) should match step 1 ({first_num})")

    # Now simulate another client incrementing on the server
    import client as client_module
    server_num, _ = client_module.get_build_number(
        project_key, server_url=running_server,
        local_file=str(tmp_path / "other_client_counter.txt"))
    assert server_num == first_num + 1, (
        f"External increment should be {first_num + 1}, got {server_num}")

    # Re-configure — step 1 gets first_num+2 from server, step 2 must also
    # return first_num+2 (the new local value), NOT first_num+1 from server
    cache = build_dir / "CMakeCache.txt"
    if cache.exists():
        cache.unlink()

    result = cmake_configure(source_dir, build_dir)
    assert result.returncode == 0, f"Second configure failed:\n{result.stderr}"
    combined = result.stdout + result.stderr
    match = re.search(r"STEP1=(\d+)", combined)
    assert match, f"STEP1 not found in second configure:\n{combined}"
    second_num = int(match.group(1))
    assert second_num == first_num + 2, (
        f"After external increment, server should return {first_num + 2}, got {second_num}")

    header_num2 = extract_build_number_from_header(version_h.read_text())
    assert header_num2 == second_num, (
        f"NO_INCREMENT header ({header_num2}) must match step 1 ({second_num}), "
        f"not the intermediate server value ({first_num + 1})")


@pytest.mark.cmake
def test_server_auth_wrong_project_fails_build(tmp_path, cmake_auth_server):
    """Valid token but wrong project causes rejection (401); build fails."""
    url = cmake_auth_server['url']
    token = cmake_auth_server['project_token']  # only for "cmake-test-project"
    source_dir = write_temp_cmakelists(
        tmp_path, _build_mode_cmakelists(
            server_url=url, server_token=token, project_key="other-project"))
    (source_dir / "main.cpp").write_text('#include "version.h"\nint main() { return 0; }\n')

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    result = cmake_configure(source_dir, build_dir)
    assert result.returncode == 0, f"Configure failed:\n{result.stderr}"

    result = cmake_build(build_dir)
    assert result.returncode != 0, "Build should fail on auth rejection"
    combined = result.stdout + result.stderr
    assert "Server rejected" in combined or "does not have access" in combined
