cmake_minimum_required(VERSION 3.20)

# Define project with static version (last component = 0)
project(SimpleExample VERSION 2.1.3.0 LANGUAGES CXX)

# Add the build-number-counter module
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../../src")
include(CMakeBuildNumber)

# Generate version header with auto-incremented build number (at build time)
increment_build_number(
    PROJECT_KEY "example-simple"
    VERSION_HEADER "${CMAKE_CURRENT_BINARY_DIR}/generated/version.h"
)

# Create executable
add_executable(simple_example main.cpp)

# Include directory for generated header
target_include_directories(simple_example PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated")

message(STATUS "Project: ${PROJECT_NAME}")
message(STATUS "Version: ${PROJECT_VERSION}")
message(STATUS "Build number will be incremented at build time!")
message(STATUS "Run: cmake --build .")

