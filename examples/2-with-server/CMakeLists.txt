cmake_minimum_required(VERSION 3.20)

# Define project with static version (last component = 0)
project(ServerExample VERSION 2.5.1.0 LANGUAGES CXX)

# Add the build-number-counter module
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../../src")
include(CMakeBuildNumber)

# Example with explicit server URL
# The server URL can also be set via BUILD_SERVER_URL environment variable
increment_build_number(
    PROJECT_KEY "example-with-server"
    VERSION_HEADER "${CMAKE_CURRENT_BINARY_DIR}/generated/version.h"
    SERVER_URL "http://localhost:8080"
)

# Create executable
add_executable(server_example main.cpp)
target_include_directories(server_example PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated")

message(STATUS "========================================")
message(STATUS "Example 2: Using Central Build Server")
message(STATUS "========================================")
message(STATUS "Project: ${PROJECT_NAME}")
message(STATUS "Version: ${PROJECT_VERSION}")
message(STATUS "")
message(STATUS "This example connects to a build server.")
message(STATUS "Make sure the server is running:")
message(STATUS "  cd ../../src")
message(STATUS "  python server.py --accept-unknown")
message(STATUS "")
message(STATUS "If server is unavailable, local fallback will be used.")
message(STATUS "Build number will increment at build time!")
message(STATUS "========================================")

