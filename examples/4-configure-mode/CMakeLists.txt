cmake_minimum_required(VERSION 3.20)

# Add the build-number-counter module
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../../src")
include(CMakeBuildNumber)

# Get build number BEFORE project() — one call, no header needed
increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "example-configure"
    OUTPUT_VARIABLE BUILD_NUM
    QUIET
)

# Define project with build number as TWEAK component
project(ConfigureExample VERSION 1.0.0.${BUILD_NUM} LANGUAGES CXX)

# Use PROJECT_VERSION directly via compile definitions — no version.h needed
add_executable(configure_example main.cpp)
target_compile_definitions(configure_example PRIVATE
    "APP_VERSION_STRING=\"${PROJECT_VERSION}\""
    APP_VERSION_BUILD=${PROJECT_VERSION_TWEAK}
)

message(STATUS "Project: ${PROJECT_NAME}")
message(STATUS "Full version: ${PROJECT_VERSION}")
message(STATUS "Build number embedded in PROJECT_VERSION at configure time!")
