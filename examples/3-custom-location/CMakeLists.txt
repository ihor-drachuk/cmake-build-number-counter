cmake_minimum_required(VERSION 3.20)

# Define project with static version (last component = 0)
project(CustomLocationExample VERSION 3.1.4.0 LANGUAGES CXX)

# Add the build-number-counter module
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../../src")
include(CMakeBuildNumber)

# Example with custom local file location
# You can specify where the local counter is stored
# This is useful if you want to commit the counter to version control
increment_build_number(
    PROJECT_KEY "example-custom-location"
    VERSION_HEADER "${CMAKE_CURRENT_BINARY_DIR}/version.h"
    LOCAL_FILE "${CMAKE_CURRENT_SOURCE_DIR}/my_build_counter.txt"
)

# Create executable
add_executable(custom_example main.cpp)
target_include_directories(custom_example PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")

message(STATUS "========================================")
message(STATUS "Example 3: Custom Counter Location")
message(STATUS "========================================")
message(STATUS "Project: ${PROJECT_NAME}")
message(STATUS "Version: ${PROJECT_VERSION}")
message(STATUS "Counter file: my_build_counter.txt")
message(STATUS "")
message(STATUS "The counter is stored in the source directory,")
message(STATUS "so it can be committed to version control if needed.")
message(STATUS "Build number will increment at build time!")
message(STATUS "========================================")

