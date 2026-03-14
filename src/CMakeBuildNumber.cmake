# CMake Build Number Module
#
# Provides functions to automatically increment build numbers for CMake projects.
# Supports central server with local fallback for offline builds.
# Two modes: BUILD (default, custom target) and CONFIGURE (execute at configure time).

# Find Python interpreter
if(NOT Python3_EXECUTABLE)
    find_package(Python3 COMPONENTS Interpreter REQUIRED)
endif()

#[=======================================================================[.rst:
increment_build_number
-----------------------

Generates version header with auto-incremented build number.

::

  increment_build_number(
    PROJECT_KEY <key>
    [VERSION_HEADER <path>]
    [MODE <BUILD|CONFIGURE>]
    [OUTPUT_VARIABLE <var>]
    [SERVER_URL <url>]
    [SERVER_TOKEN <token>]
    [LOCAL_FILE <path>]
    [TARGET <target>]
    [FORCE_VERSION <N>]
    [NO_INCREMENT]
    [QUIET]
  )

Arguments:
  PROJECT_KEY - Unique identifier for the project (required)
  VERSION_HEADER - Output header file path (required in BUILD mode, optional in CONFIGURE mode)
  MODE - BUILD (default) or CONFIGURE. BUILD creates a custom target; CONFIGURE runs at configure time
  OUTPUT_VARIABLE - Variable to receive build number (CONFIGURE mode only)
  SERVER_URL - URL of the build number server (optional, uses BUILD_SERVER_URL env var if not set)
  SERVER_TOKEN - API token for server authentication (optional, uses BUILD_SERVER_TOKEN env var if not set)
  LOCAL_FILE - Path to local counter file for offline fallback (optional, defaults to build dir)
  TARGET - Target name to attach version generation (BUILD mode only, auto-generated if not specified)
  FORCE_VERSION - Force-set build number to this value instead of incrementing (optional)
  NO_INCREMENT - Read current counter value without incrementing (mutually exclusive with FORCE_VERSION)
  QUIET - Suppress client log messages

BUILD mode (default):
  Generates header with APP_VERSION_MAJOR, APP_VERSION_MINOR, APP_VERSION_PATCH (from PROJECT_VERSION),
  APP_VERSION_BUILD (auto-incremented), APP_VERSION_STRING (full version string).
  Requires project(VERSION ...) to have been called.

CONFIGURE mode:
  Runs increment at configure time. Can be called before project() if only OUTPUT_VARIABLE is used.
  VERSION_HEADER requires project(VERSION ...) to have been called.
  Automatically forces reconfigure on every build (via a stamp file that is deleted at build time),
  unless NO_INCREMENT is used.

Example (BUILD mode):
  project(MyApp VERSION 1.2.3.0)  # Last component should be 0

  increment_build_number(
    PROJECT_KEY "myproject"
    VERSION_HEADER "${CMAKE_BINARY_DIR}/generated/version.h"
  )

Example (CONFIGURE mode):
  increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "myproject"
    OUTPUT_VARIABLE BUILD_NUM
  )
  project(MyApp VERSION 1.2.3.${BUILD_NUM})

#]=======================================================================]
function(increment_build_number)
    # Parse arguments
    set(options QUIET NO_INCREMENT)
    set(oneValueArgs PROJECT_KEY VERSION_HEADER SERVER_URL SERVER_TOKEN LOCAL_FILE TARGET FORCE_VERSION MODE OUTPUT_VARIABLE)
    set(multiValueArgs)
    cmake_parse_arguments(ARG "${options}" "${oneValueArgs}" "${multiValueArgs}" ${ARGN})

    # --- Validate common arguments ---

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

    # Default MODE to BUILD
    if(NOT ARG_MODE)
        set(ARG_MODE "BUILD")
    endif()

    # Validate MODE
    if(NOT ARG_MODE STREQUAL "BUILD" AND NOT ARG_MODE STREQUAL "CONFIGURE")
        message(FATAL_ERROR "increment_build_number: MODE must be BUILD or CONFIGURE, got '${ARG_MODE}'")
    endif()

    # Validate NO_INCREMENT + FORCE_VERSION are mutually exclusive
    if(ARG_NO_INCREMENT AND ARG_FORCE_VERSION)
        message(FATAL_ERROR "increment_build_number: NO_INCREMENT and FORCE_VERSION are mutually exclusive")
    endif()

    # Validate FORCE_VERSION format
    if(ARG_FORCE_VERSION)
        if(NOT ARG_FORCE_VERSION MATCHES "^[0-9]+$")
            message(FATAL_ERROR
                "increment_build_number: FORCE_VERSION must be a non-negative integer, got '${ARG_FORCE_VERSION}'")
        endif()
    endif()

    # Set defaults
    if(NOT ARG_LOCAL_FILE)
        set(ARG_LOCAL_FILE "${CMAKE_BINARY_DIR}/build_number.txt")
    endif()

    # Find the client script
    set(CLIENT_SCRIPT "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/client.py")
    if(NOT EXISTS "${CLIENT_SCRIPT}")
        message(FATAL_ERROR "Cannot find client.py at ${CLIENT_SCRIPT}")
    endif()

    # --- Mode-specific validation and warnings ---

    if(ARG_MODE STREQUAL "BUILD")
        # BUILD mode: VERSION_HEADER is required
        if(NOT ARG_VERSION_HEADER)
            message(FATAL_ERROR "increment_build_number: VERSION_HEADER is required in BUILD mode")
        endif()

        # Warn if OUTPUT_VARIABLE is used in BUILD mode (ignored)
        if(ARG_OUTPUT_VARIABLE)
            message(WARNING "increment_build_number: OUTPUT_VARIABLE is ignored in BUILD mode")
        endif()

        # Default target name
        if(NOT ARG_TARGET)
            set(ARG_TARGET "generate_version_${ARG_PROJECT_KEY}")
        endif()

        # Require PROJECT_VERSION
        if(NOT PROJECT_VERSION)
            message(FATAL_ERROR "increment_build_number: PROJECT_VERSION is not set. Call project(VERSION ...) first.")
        endif()

    elseif(ARG_MODE STREQUAL "CONFIGURE")
        # CONFIGURE mode: at least one of VERSION_HEADER or OUTPUT_VARIABLE must be set
        if(NOT ARG_VERSION_HEADER AND NOT ARG_OUTPUT_VARIABLE)
            message(FATAL_ERROR "increment_build_number: CONFIGURE mode requires VERSION_HEADER and/or OUTPUT_VARIABLE")
        endif()

        # Warn if TARGET is used in CONFIGURE mode (ignored)
        if(ARG_TARGET)
            message(WARNING "increment_build_number: TARGET is ignored in CONFIGURE mode (no custom target created)")
        endif()

        # VERSION_HEADER requires PROJECT_VERSION
        if(ARG_VERSION_HEADER AND NOT PROJECT_VERSION)
            message(FATAL_ERROR
                "increment_build_number: VERSION_HEADER requires PROJECT_VERSION to be set."
                " Call project(VERSION ...) first, or use only OUTPUT_VARIABLE.")
        endif()
    endif()

    # --- CONFIGURE mode ---

    if(ARG_MODE STREQUAL "CONFIGURE")

        if(ARG_NO_INCREMENT)
            # NO_INCREMENT: read current value from local file directly, no client.py call
            if(NOT EXISTS "${ARG_LOCAL_FILE}")
                message(FATAL_ERROR
                    "increment_build_number: NO_INCREMENT requires an existing counter file,"
                    " but '${ARG_LOCAL_FILE}' does not exist. Run a normal increment first.")
            endif()
            file(READ "${ARG_LOCAL_FILE}" _build_num)
            string(STRIP "${_build_num}" _build_num)
            if(NOT _build_num MATCHES "^[0-9]+$")
                message(FATAL_ERROR
                    "increment_build_number: Invalid content in counter file '${ARG_LOCAL_FILE}': '${_build_num}'")
            endif()
            set(BUILD_NUM_OUTPUT "${_build_num}")

        else()
            # Normal increment or FORCE_VERSION: call client.py
            set(CLIENT_ARGS
                "${CLIENT_SCRIPT}"
                --project-key "${ARG_PROJECT_KEY}"
                --local-file "${ARG_LOCAL_FILE}"
                --output-format plain
            )

            if(ARG_SERVER_URL)
                list(APPEND CLIENT_ARGS --server-url "${ARG_SERVER_URL}")
            endif()

            if(ARG_SERVER_TOKEN)
                list(APPEND CLIENT_ARGS --server-token "${ARG_SERVER_TOKEN}")
            endif()

            if(ARG_QUIET)
                list(APPEND CLIENT_ARGS --quiet)
            endif()

            if(ARG_FORCE_VERSION)
                list(APPEND CLIENT_ARGS --force-version ${ARG_FORCE_VERSION})
            endif()

            execute_process(
                COMMAND "${Python3_EXECUTABLE}" ${CLIENT_ARGS}
                OUTPUT_VARIABLE BUILD_NUM_OUTPUT
                ERROR_VARIABLE BUILD_NUM_ERROR
                RESULT_VARIABLE BUILD_NUM_RESULT
                OUTPUT_STRIP_TRAILING_WHITESPACE
                ERROR_STRIP_TRAILING_WHITESPACE
            )

            if(NOT BUILD_NUM_RESULT EQUAL 0)
                message(FATAL_ERROR "increment_build_number: Failed to get build number: ${BUILD_NUM_ERROR}")
            endif()

            # Print client logs (sent to stderr)
            if(BUILD_NUM_ERROR)
                message(STATUS "${BUILD_NUM_ERROR}")
            endif()

            if(NOT BUILD_NUM_OUTPUT MATCHES "^[0-9]+$")
                message(FATAL_ERROR "increment_build_number: Invalid build number received: '${BUILD_NUM_OUTPUT}'")
            endif()

            # Auto-reconfigure: force reconfigure on every cmake --build.
            # Mechanism: create a stamp file at configure time, depend on it,
            # and add a custom target that deletes it at build time.
            # Next build sees stamp missing/changed → triggers reconfigure → cycle repeats.
            set(_reconfigure_stamp "${CMAKE_BINARY_DIR}/_build_number_reconfigure_stamp_${ARG_PROJECT_KEY}")
            file(WRITE "${_reconfigure_stamp}" "${BUILD_NUM_OUTPUT}")
            set_property(DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}" APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
                "${_reconfigure_stamp}")
            if(NOT TARGET _build_number_invalidate_${ARG_PROJECT_KEY})
                add_custom_target(_build_number_invalidate_${ARG_PROJECT_KEY} ALL
                    COMMAND ${CMAKE_COMMAND} -E remove "${_reconfigure_stamp}"
                    COMMENT "Invalidating build number configure stamp for ${ARG_PROJECT_KEY}"
                )
            endif()
        endif()

        message(STATUS "Build number for '${ARG_PROJECT_KEY}': ${BUILD_NUM_OUTPUT}")

        # Set OUTPUT_VARIABLE in parent scope
        if(ARG_OUTPUT_VARIABLE)
            set(${ARG_OUTPUT_VARIABLE} "${BUILD_NUM_OUTPUT}" PARENT_SCOPE)
        endif()

        # Generate VERSION_HEADER if requested (requires PROJECT_VERSION, already validated above)
        if(ARG_VERSION_HEADER)
            set(VERSION_MAJOR ${PROJECT_VERSION_MAJOR})
            set(VERSION_MINOR ${PROJECT_VERSION_MINOR})
            set(VERSION_PATCH ${PROJECT_VERSION_PATCH})

            set(VERSION_HEADER_CONTENT "#pragma once

// Auto-generated version header
// Do not edit manually

#define APP_VERSION_MAJOR ${VERSION_MAJOR}
#define APP_VERSION_MINOR ${VERSION_MINOR}
#define APP_VERSION_PATCH ${VERSION_PATCH}
#define APP_VERSION_BUILD ${BUILD_NUM_OUTPUT}
#define APP_VERSION_STRING \"${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_PATCH}.${BUILD_NUM_OUTPUT}\"
")
            file(WRITE "${ARG_VERSION_HEADER}" "${VERSION_HEADER_CONTENT}")
            message(STATUS "Generated version header: ${ARG_VERSION_HEADER}")
        endif()

        return()
    endif()

    # --- BUILD mode (original behavior) ---

    # Require PROJECT_VERSION (already validated above)
    set(VERSION_MAJOR ${PROJECT_VERSION_MAJOR})
    set(VERSION_MINOR ${PROJECT_VERSION_MINOR})
    set(VERSION_PATCH ${PROJECT_VERSION_PATCH})

    # Create a CMake script that will run at build time
    set(GENERATOR_SCRIPT "${CMAKE_BINARY_DIR}/_generate_version_${ARG_PROJECT_KEY}.cmake")

    if(ARG_NO_INCREMENT)
        # NO_INCREMENT in BUILD mode: read from file instead of calling client.py
        file(WRITE "${GENERATOR_SCRIPT}" "
# Auto-generated script to read build number and generate version header (NO_INCREMENT)

set(LOCAL_FILE \"${ARG_LOCAL_FILE}\")
if(NOT EXISTS \"\${LOCAL_FILE}\")
    message(FATAL_ERROR \"NO_INCREMENT requires an existing counter file, but '\${LOCAL_FILE}' does not exist.\")
endif()
file(READ \"\${LOCAL_FILE}\" BUILD_NUM_OUTPUT)
string(STRIP \"\${BUILD_NUM_OUTPUT}\" BUILD_NUM_OUTPUT)
if(NOT BUILD_NUM_OUTPUT MATCHES \"^[0-9]+$\")
    message(FATAL_ERROR \"Invalid content in counter file: '\${BUILD_NUM_OUTPUT}'\")
endif()

message(STATUS \"Build number for '${ARG_PROJECT_KEY}' (read-only): \${BUILD_NUM_OUTPUT}\")

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
    else()
        # Normal increment or FORCE_VERSION: call client.py
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
    endif()

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
