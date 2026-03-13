# CMake Build Number Module
#
# Provides functions to automatically increment build numbers for CMake projects.
# Supports central server with local fallback for offline builds.

# Find Python interpreter
if(NOT Python3_EXECUTABLE)
    find_package(Python3 COMPONENTS Interpreter REQUIRED)
endif()

#[=======================================================================[.rst:
increment_build_number
-----------------------

Generates version header with auto-incremented build number at build time.

::

  increment_build_number(
    PROJECT_KEY <key>
    VERSION_HEADER <path>
    [SERVER_URL <url>]
    [SERVER_TOKEN <token>]
    [LOCAL_FILE <path>]
    [TARGET <target>]
    [FORCE_VERSION <N>]
    [QUIET]
  )

Arguments:
  PROJECT_KEY - Unique identifier for the project (required)
  VERSION_HEADER - Output header file path, e.g., "${CMAKE_BINARY_DIR}/version.h" (required)
  SERVER_URL - URL of the build number server (optional, uses BUILD_SERVER_URL env var if not set)
  SERVER_TOKEN - API token for server authentication (optional, uses BUILD_SERVER_TOKEN env var if not set)
  LOCAL_FILE - Path to local counter file for offline fallback (optional, defaults to build dir)
  TARGET - Target name to attach version generation (optional, auto-generated if not specified)
  FORCE_VERSION - Force-set build number to this value instead of incrementing (optional)
  QUIET - Suppress client log messages

Generates header with:
  APP_VERSION_MAJOR, APP_VERSION_MINOR, APP_VERSION_PATCH (from PROJECT_VERSION)
  APP_VERSION_BUILD (auto-incremented)
  APP_VERSION_STRING (full version string)

Example:
  project(MyApp VERSION 1.2.3.0)  # Last component should be 0

  increment_build_number(
    PROJECT_KEY "myproject"
    VERSION_HEADER "${CMAKE_BINARY_DIR}/version.h"
    SERVER_URL "http://your-server.com:8080"
  )

  add_executable(myapp main.cpp)
  target_include_directories(myapp PRIVATE ${CMAKE_BINARY_DIR})

#]=======================================================================]
function(increment_build_number)
    # Parse arguments
    set(options QUIET)
    set(oneValueArgs PROJECT_KEY VERSION_HEADER SERVER_URL SERVER_TOKEN LOCAL_FILE TARGET FORCE_VERSION)
    set(multiValueArgs)
    cmake_parse_arguments(ARG "${options}" "${oneValueArgs}" "${multiValueArgs}" ${ARGN})

    # Validate required arguments
    if(NOT ARG_PROJECT_KEY)
        message(FATAL_ERROR "increment_build_number: PROJECT_KEY is required")
    endif()

    # Validate key format
    string(LENGTH "${ARG_PROJECT_KEY}" _key_length)
    if(_key_length GREATER 128)
        message(FATAL_ERROR
            "increment_build_number: Invalid PROJECT_KEY '${ARG_PROJECT_KEY}':"
            " must be 1-128 characters, allowed: [a-zA-Z0-9._-]")
    endif()
    string(REGEX MATCH "^[a-zA-Z0-9._-]+$" _key_match "${ARG_PROJECT_KEY}")
    if(NOT _key_match)
        message(FATAL_ERROR
            "increment_build_number: Invalid PROJECT_KEY '${ARG_PROJECT_KEY}':"
            " must be 1-128 characters, allowed: [a-zA-Z0-9._-]")
    endif()

    if(NOT ARG_VERSION_HEADER)
        message(FATAL_ERROR "increment_build_number: VERSION_HEADER is required")
    endif()

    # Set defaults
    if(NOT ARG_LOCAL_FILE)
        set(ARG_LOCAL_FILE "${CMAKE_BINARY_DIR}/build_number.txt")
    endif()

    if(NOT ARG_TARGET)
        set(ARG_TARGET "generate_version_${ARG_PROJECT_KEY}")
    endif()

    # Get project version components
    if(NOT PROJECT_VERSION)
        message(FATAL_ERROR "increment_build_number: PROJECT_VERSION is not set. Call project(VERSION ...) first.")
    endif()

    set(VERSION_MAJOR ${PROJECT_VERSION_MAJOR})
    set(VERSION_MINOR ${PROJECT_VERSION_MINOR})
    set(VERSION_PATCH ${PROJECT_VERSION_PATCH})

    # Find the client script
    set(CLIENT_SCRIPT "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/client.py")
    if(NOT EXISTS "${CLIENT_SCRIPT}")
        message(FATAL_ERROR "Cannot find client.py at ${CLIENT_SCRIPT}")
    endif()

    # Create a CMake script that will run at build time
    set(GENERATOR_SCRIPT "${CMAKE_BINARY_DIR}/_generate_version_${ARG_PROJECT_KEY}.cmake")

    file(WRITE "${GENERATOR_SCRIPT}" "
# Auto-generated script to increment build number and generate version header

# Build command arguments
set(CLIENT_ARGS
    \"${CLIENT_SCRIPT}\"
    --project-key \"${ARG_PROJECT_KEY}\"
    --local-file \"${ARG_LOCAL_FILE}\"
    --output-format plain
")

    if(ARG_SERVER_URL)
        file(APPEND "${GENERATOR_SCRIPT}" "    --server-url \"${ARG_SERVER_URL}\"\n")
    endif()

    if(ARG_SERVER_TOKEN)
        file(APPEND "${GENERATOR_SCRIPT}" "    --server-token \"${ARG_SERVER_TOKEN}\"\n")
    endif()

    if(ARG_QUIET)
        file(APPEND "${GENERATOR_SCRIPT}" "    --quiet\n")
    endif()

    if(ARG_FORCE_VERSION)
        if(NOT ARG_FORCE_VERSION MATCHES "^[0-9]+$")
            message(FATAL_ERROR
                "increment_build_number: FORCE_VERSION must be a non-negative integer, got '${ARG_FORCE_VERSION}'")
        endif()
        file(APPEND "${GENERATOR_SCRIPT}" "    --force-version ${ARG_FORCE_VERSION}\n")
    endif()

    file(APPEND "${GENERATOR_SCRIPT}" ")

# Execute the client script
execute_process(
    COMMAND \"${Python3_EXECUTABLE}\" \${CLIENT_ARGS}
    OUTPUT_VARIABLE BUILD_NUM_OUTPUT
    ERROR_VARIABLE BUILD_NUM_ERROR
    RESULT_VARIABLE BUILD_NUM_RESULT
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_STRIP_TRAILING_WHITESPACE
)

# Check result
if(NOT BUILD_NUM_RESULT EQUAL 0)
    message(FATAL_ERROR \"Failed to get build number: \${BUILD_NUM_ERROR}\")
endif()

# Print logs
if(BUILD_NUM_ERROR)
    message(STATUS \"\${BUILD_NUM_ERROR}\")
endif()

# Validate output is a number
if(NOT BUILD_NUM_OUTPUT MATCHES \"^[0-9]+$\")
    message(FATAL_ERROR \"Invalid build number received: \${BUILD_NUM_OUTPUT}\")
endif()

message(STATUS \"Build number for '${ARG_PROJECT_KEY}': \${BUILD_NUM_OUTPUT}\")

# Generate version header
set(VERSION_HEADER_CONTENT \"#pragma once

// Auto-generated version header
// Do not edit manually

#define APP_VERSION_MAJOR ${VERSION_MAJOR}
#define APP_VERSION_MINOR ${VERSION_MINOR}
#define APP_VERSION_PATCH ${VERSION_PATCH}
#define APP_VERSION_BUILD \${BUILD_NUM_OUTPUT}
#define APP_VERSION_STRING \\\"${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_PATCH}.\${BUILD_NUM_OUTPUT}\\\"
\")

file(WRITE \"${ARG_VERSION_HEADER}\" \"\${VERSION_HEADER_CONTENT}\")
message(STATUS \"Generated version header: ${ARG_VERSION_HEADER}\")
")

    # Create custom target that runs the generator script
    add_custom_target(${ARG_TARGET}
        ALL
        COMMAND ${CMAKE_COMMAND} -P "${GENERATOR_SCRIPT}"
        BYPRODUCTS "${ARG_VERSION_HEADER}"
        COMMENT "Incrementing build number for ${ARG_PROJECT_KEY}"
        VERBATIM
    )

    message(STATUS "Build number will be incremented at build time for '${ARG_PROJECT_KEY}'")
    message(STATUS "Version header: ${ARG_VERSION_HEADER}")
endfunction()


