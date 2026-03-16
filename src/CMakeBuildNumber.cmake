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

Generates ``cbnc-version.h`` header with auto-incremented build number.

::

  increment_build_number(
    PROJECT_KEY <key>
    [MODE <BUILD|CONFIGURE>]
    [OUTPUT_VARIABLE <var>]
    [SERVER_URL <url>]
    [SERVER_TOKEN <token>]
    [LOCAL_FILE <path>]
    [TARGET <target>]
    [FORCE_VERSION <N>]
    [REUSE_COUNTER]
    [DISABLED]
    [QUIET]
  )

Arguments:
  PROJECT_KEY - Unique identifier for the project (required)
  MODE - BUILD (default) or CONFIGURE. BUILD creates a custom target; CONFIGURE runs at configure time
  OUTPUT_VARIABLE - Variable to receive build number (CONFIGURE mode only)
  SERVER_URL - URL of the build number server (optional, uses BUILD_SERVER_URL env var if not set)
  SERVER_TOKEN - API token for server authentication (optional, uses BUILD_SERVER_TOKEN env var if not set)
  LOCAL_FILE - Path to local counter file for offline fallback (optional, defaults to build dir)
  TARGET - Custom target name (BUILD mode only, internal default: _cbnc_generate)
  FORCE_VERSION - Force-set build number to this value instead of incrementing (optional)
  REUSE_COUNTER - Read current counter value without incrementing. For the two-step CONFIGURE
    mode pattern: increment before project(), REUSE_COUNTER after project() to generate the header.
    Mutually exclusive with FORCE_VERSION.
  DISABLED - Skip incrementing if build artifacts already exist (for IDE/debug presets).
    On first run (no files), performs a full bootstrap. Compatible with REUSE_COUNTER and FORCE_VERSION.
  QUIET - Suppress client log messages

BUILD mode (default):
  Generates ``${CMAKE_BINARY_DIR}/cbnc-generated/cbnc-version.h`` with APP_VERSION_MAJOR, APP_VERSION_MINOR,
  APP_VERSION_PATCH (from PROJECT_VERSION), APP_VERSION_BUILD (auto-incremented),
  APP_VERSION_STRING (full version string). Requires project(VERSION ...) to have been called.
  Creates ``cbnc::version`` INTERFACE library target that propagates include directory and
  build dependency. Use ``target_link_libraries(myapp PRIVATE cbnc::version)`` to consume.

CONFIGURE mode:
  Runs increment at configure time. Can be called before project() with OUTPUT_VARIABLE.
  When PROJECT_VERSION is set (after project()), automatically generates ``cbnc-version.h``
  and creates ``cbnc::version`` INTERFACE library.
  Automatically forces reconfigure on every build (via stamp file), unless REUSE_COUNTER or DISABLED is used.

Example (BUILD mode):
  project(MyApp VERSION 1.2.3.0)  # Last component should be 0

  increment_build_number(
    PROJECT_KEY "myproject"
  )

  add_executable(myapp main.cpp)
  target_link_libraries(myapp PRIVATE cbnc::version)

Example (CONFIGURE mode, two-step pattern):
  increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "myproject"
    OUTPUT_VARIABLE BUILD_NUM
  )
  project(MyApp VERSION 1.2.3.${BUILD_NUM})
  increment_build_number(
    MODE CONFIGURE
    PROJECT_KEY "myproject"
    REUSE_COUNTER
  )
  # cbnc-version.h is now generated, cbnc::version target is available

#]=======================================================================]
function(increment_build_number)
    # Parse arguments
    set(options QUIET REUSE_COUNTER DISABLED)
    set(oneValueArgs PROJECT_KEY SERVER_URL SERVER_TOKEN LOCAL_FILE TARGET FORCE_VERSION MODE OUTPUT_VARIABLE)
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

    # Validate REUSE_COUNTER + FORCE_VERSION are mutually exclusive
    if(ARG_REUSE_COUNTER AND ARG_FORCE_VERSION)
        message(FATAL_ERROR "increment_build_number: REUSE_COUNTER and FORCE_VERSION are mutually exclusive")
    endif()
    # DISABLED + REUSE_COUNTER: allowed (DISABLED adds "skip if header exists" on top)
    # DISABLED + FORCE_VERSION: allowed (DISABLED is ignored, FORCE_VERSION is idempotent)

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

    # Hardcoded version header path
    set(_CBNC_VERSION_HEADER "${CMAKE_BINARY_DIR}/cbnc-generated/cbnc-version.h")

    # Find the client script
    set(CLIENT_SCRIPT "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/client.py")
    if(NOT EXISTS "${CLIENT_SCRIPT}")
        message(FATAL_ERROR "Cannot find client.py at ${CLIENT_SCRIPT}")
    endif()

    # --- Mode-specific validation and warnings ---

    if(ARG_MODE STREQUAL "BUILD")
        # Warn if OUTPUT_VARIABLE is used in BUILD mode (ignored)
        if(ARG_OUTPUT_VARIABLE)
            message(WARNING "increment_build_number: OUTPUT_VARIABLE is ignored in BUILD mode")
        endif()

        # Default target name (internal; users should link cbnc::version instead)
        if(NOT ARG_TARGET)
            set(ARG_TARGET "_cbnc_generate")
        endif()

        # Require PROJECT_VERSION
        if(NOT PROJECT_VERSION)
            message(FATAL_ERROR "increment_build_number: PROJECT_VERSION is not set. Call project(VERSION ...) first.")
        endif()

    elseif(ARG_MODE STREQUAL "CONFIGURE")
        # CONFIGURE mode: OUTPUT_VARIABLE is needed if PROJECT_VERSION is not set
        if(NOT OUTPUT_VARIABLE AND NOT PROJECT_VERSION AND NOT ARG_OUTPUT_VARIABLE)
            message(FATAL_ERROR
                "increment_build_number: CONFIGURE mode requires OUTPUT_VARIABLE (before project())"
                " or PROJECT_VERSION to be set (after project()).")
        endif()

        # Warn if TARGET is used in CONFIGURE mode (ignored)
        if(ARG_TARGET)
            message(WARNING "increment_build_number: TARGET is ignored in CONFIGURE mode (no custom target created)")
        endif()
    endif()

    # --- CONFIGURE mode ---

    if(ARG_MODE STREQUAL "CONFIGURE")

            # --- DISABLED logic (CONFIGURE mode) ---
        # DISABLED + FORCE_VERSION: DISABLED is ignored, fall through to normal FORCE_VERSION
        if(ARG_DISABLED AND NOT ARG_FORCE_VERSION)
            # Build the skipped command string for log messages
            set(_cbnc_skipped_cmd "${Python3_EXECUTABLE} ${CLIENT_SCRIPT} --project-key ${ARG_PROJECT_KEY} --local-file ${ARG_LOCAL_FILE} --output-format plain")
            if(ARG_SERVER_URL)
                string(APPEND _cbnc_skipped_cmd " --server-url ${ARG_SERVER_URL}")
            endif()
            if(ARG_SERVER_TOKEN)
                string(APPEND _cbnc_skipped_cmd " --server-token ***")
            endif()
            if(ARG_QUIET)
                string(APPEND _cbnc_skipped_cmd " --quiet")
            endif()

            if(ARG_REUSE_COUNTER)
                # DISABLED + REUSE_COUNTER: skip if artifacts exist, else generate from counter.
                # Before project(): header can't be generated, so counter file alone is enough.
                # After project(): need header to exist too.
                set(_cbnc_can_skip_reuse FALSE)
                if(EXISTS "${ARG_LOCAL_FILE}")
                    if(NOT PROJECT_VERSION)
                        set(_cbnc_can_skip_reuse TRUE)
                    elseif(EXISTS "${_CBNC_VERSION_HEADER}")
                        set(_cbnc_can_skip_reuse TRUE)
                    endif()
                endif()
                if(_cbnc_can_skip_reuse)
                    if(ARG_OUTPUT_VARIABLE)
                        file(READ "${ARG_LOCAL_FILE}" _build_num)
                        string(STRIP "${_build_num}" _build_num)
                        set(${ARG_OUTPUT_VARIABLE} "${_build_num}" PARENT_SCOPE)
                    endif()
                    message(STATUS "[CBNC] Skipped (DISABLED): ${ARG_PROJECT_KEY}")
                    return()
                endif()
                # Header missing (post-project) — fall through to REUSE_COUNTER to generate it
            else()
                # DISABLED alone (no REUSE_COUNTER, no FORCE_VERSION)
                # Skip condition depends on whether header generation is possible:
                # - After project(): need both counter file + header to skip
                # - Before project(): header can't be generated, so counter file alone is enough
                set(_cbnc_can_skip FALSE)
                if(EXISTS "${ARG_LOCAL_FILE}")
                    if(NOT PROJECT_VERSION)
                        # Pre-project: header can't exist, counter file is sufficient for skip
                        set(_cbnc_can_skip TRUE)
                    elseif(EXISTS "${_CBNC_VERSION_HEADER}")
                        # Post-project: both counter + header exist
                        set(_cbnc_can_skip TRUE)
                    endif()
                endif()

                if(_cbnc_can_skip)
                    # Skip — read counter value, log the skipped command
                    file(READ "${ARG_LOCAL_FILE}" _build_num)
                    string(STRIP "${_build_num}" _build_num)
                    if(ARG_OUTPUT_VARIABLE)
                        set(${ARG_OUTPUT_VARIABLE} "${_build_num}" PARENT_SCOPE)
                    endif()
                    message(STATUS "[CBNC] Skipped: ${_cbnc_skipped_cmd}")
                    return()
                elseif(EXISTS "${ARG_LOCAL_FILE}" AND NOT EXISTS "${_CBNC_VERSION_HEADER}")
                    # Counter exists but header missing (post-project) — generate header from counter
                    file(READ "${ARG_LOCAL_FILE}" _build_num)
                    string(STRIP "${_build_num}" _build_num)
                    if(NOT _build_num MATCHES "^[0-9]+$")
                        message(FATAL_ERROR
                            "increment_build_number: Invalid content in counter file '${ARG_LOCAL_FILE}': '${_build_num}'")
                    endif()
                    set(BUILD_NUM_OUTPUT "${_build_num}")
                    message(STATUS "[CBNC] Build number for '${ARG_PROJECT_KEY}': ${BUILD_NUM_OUTPUT} (DISABLED, header regenerated)")
                    if(ARG_OUTPUT_VARIABLE)
                        set(${ARG_OUTPUT_VARIABLE} "${BUILD_NUM_OUTPUT}" PARENT_SCOPE)
                    endif()
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
                    file(WRITE "${_CBNC_VERSION_HEADER}" "${VERSION_HEADER_CONTENT}")
                    message(STATUS "[CBNC] Generated version header: ${_CBNC_VERSION_HEADER}")
                    if(NOT TARGET cbnc_version)
                        add_library(cbnc_version INTERFACE)
                        add_library(cbnc::version ALIAS cbnc_version)
                    endif()
                    target_include_directories(cbnc_version INTERFACE "${CMAKE_BINARY_DIR}/cbnc-generated")
                    return()
                endif()
                # No files exist — bootstrap: fall through to normal execution
                message(STATUS "[CBNC] Bootstrap (DISABLED, first run): ${ARG_PROJECT_KEY}")
            endif()
        endif()

        if(ARG_REUSE_COUNTER)
            # REUSE_COUNTER: read current value from local file directly, no client.py call.
            # Used in the two-step CONFIGURE mode pattern: increment before project(),
            # REUSE_COUNTER after project() to generate the header without double-incrementing.
            if(NOT EXISTS "${ARG_LOCAL_FILE}")
                message(FATAL_ERROR
                    "increment_build_number: REUSE_COUNTER requires an existing counter file,"
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

            # Print client logs (sent to stderr), one message(STATUS) per line
            if(BUILD_NUM_ERROR)
                string(REPLACE "\n" ";" _cbnc_log_lines "${BUILD_NUM_ERROR}")
                foreach(_cbnc_line IN LISTS _cbnc_log_lines)
                    if(NOT _cbnc_line STREQUAL "")
                        message(STATUS "${_cbnc_line}")
                    endif()
                endforeach()
            endif()

            if(NOT BUILD_NUM_OUTPUT MATCHES "^[0-9]+$")
                message(FATAL_ERROR "increment_build_number: Invalid build number received: '${BUILD_NUM_OUTPUT}'")
            endif()

            # Auto-reconfigure: force reconfigure on every cmake --build.
            # Skipped when DISABLED (no stamp file = no auto-reconfigure).
            if(NOT ARG_DISABLED)
            #
            # A stamp file registered in CMAKE_CONFIGURE_DEPENDS is modified
            # by a custom target at build time, triggering reconfigure on the
            # next build. The first build needs a bootstrap (stamp mtime <
            # build system files after configure). Strategy per generator:
            # Makefiles: far-future mtime (always newer than Makefile) + touch.
            # Ninja: +1day future mtime, epoch neutralize on reconfig, + touch.
            # Visual Studio: stamp delete (MSBuild always re-evaluates).
            set(_reconfigure_stamp "${CMAKE_BINARY_DIR}/_build_number_reconfigure_stamp_${ARG_PROJECT_KEY}")
            set_property(DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}" APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
                "${_reconfigure_stamp}")
            if(CMAKE_GENERATOR MATCHES "Makefiles")
                # Makefiles: write stamp; set future mtime on first configure
                # to bootstrap the cycle (subsequent configures are triggered
                # by touch, so stamp mtime is already newer than Makefile).
                if(NOT EXISTS "${_reconfigure_stamp}")
                    file(WRITE "${_reconfigure_stamp}" "${BUILD_NUM_OUTPUT}")
                    execute_process(
                        COMMAND "${Python3_EXECUTABLE}" -c
                            "import os; os.utime('${_reconfigure_stamp}', (9999999999, 9999999999))"
                    )
                else()
                    file(WRITE "${_reconfigure_stamp}" "${BUILD_NUM_OUTPUT}")
                endif()
                if(NOT TARGET _build_number_invalidate_${ARG_PROJECT_KEY})
                    add_custom_target(_build_number_invalidate_${ARG_PROJECT_KEY} ALL
                        COMMAND ${CMAKE_COMMAND} -E touch "${_reconfigure_stamp}"
                        COMMENT "Triggering reconfigure for build number increment (${ARG_PROJECT_KEY})"
                    )
                endif()
            elseif(CMAKE_GENERATOR MATCHES "Ninja")
                # Ninja: future mtime bootstrap + epoch neutralize.
                #
                # Ninja triggers RERUN_CMAKE when a dep's mtime > build.ninja's.
                # The stamp written during configure has mtime < build.ninja,
                # so Ninja skips reconfigure on the first build.
                #
                # Fix: on first configure, set stamp mtime far into the future.
                # Ninja sees stamp newer → RERUN_CMAKE. On that reconfigure,
                # neutralize by setting stamp mtime to epoch (0), which is
                # guaranteed < build.ninja — this stops the loop. The custom
                # target then touches the stamp (mtime = now > build.ninja),
                # priming the next build. Steady state:
                # touch → reconfig → neutralize(epoch) → touch → ...
                set(_reconfigure_sentinel "${CMAKE_BINARY_DIR}/_build_number_sentinel_${ARG_PROJECT_KEY}")
                if(NOT EXISTS "${_reconfigure_sentinel}")
                    file(WRITE "${_reconfigure_stamp}" "${BUILD_NUM_OUTPUT}")
                    execute_process(
                        COMMAND "${Python3_EXECUTABLE}" -c
                            "import os,time; os.utime('${_reconfigure_stamp}',(time.time()+86400,time.time()+86400))"
                    )
                    file(WRITE "${_reconfigure_sentinel}" "1")
                else()
                    execute_process(
                        COMMAND "${Python3_EXECUTABLE}" -c
                            "import os; os.utime('${_reconfigure_stamp}',(0,0))"
                    )
                endif()
                if(NOT TARGET _build_number_invalidate_${ARG_PROJECT_KEY})
                    add_custom_target(_build_number_invalidate_${ARG_PROJECT_KEY} ALL
                        COMMAND ${CMAKE_COMMAND} -E touch "${_reconfigure_stamp}"
                        COMMENT "Triggering reconfigure for build number increment (${ARG_PROJECT_KEY})"
                    )
                endif()
            else()
                # Visual Studio and others: write stamp, delete at build time.
                # MSBuild always re-evaluates, so stamp deletion reliably
                # triggers reconfigure.
                file(WRITE "${_reconfigure_stamp}" "${BUILD_NUM_OUTPUT}")
                if(NOT TARGET _build_number_invalidate_${ARG_PROJECT_KEY})
                    add_custom_target(_build_number_invalidate_${ARG_PROJECT_KEY} ALL
                        COMMAND ${CMAKE_COMMAND} -E remove "${_reconfigure_stamp}"
                        COMMENT "Invalidating build number configure stamp for ${ARG_PROJECT_KEY}"
                    )
                endif()
            endif()
            endif() # if(NOT ARG_DISABLED)
        endif()

        message(STATUS "[CBNC] Build number for '${ARG_PROJECT_KEY}': ${BUILD_NUM_OUTPUT}")

        # Set OUTPUT_VARIABLE in parent scope
        if(ARG_OUTPUT_VARIABLE)
            set(${ARG_OUTPUT_VARIABLE} "${BUILD_NUM_OUTPUT}" PARENT_SCOPE)
        endif()

        # Generate version header if PROJECT_VERSION is available
        if(PROJECT_VERSION)
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
            # Idempotent write: skip if content unchanged (avoids recompilation)
            set(_cbnc_write_header TRUE)
            if(EXISTS "${_CBNC_VERSION_HEADER}")
                file(READ "${_CBNC_VERSION_HEADER}" _cbnc_existing_header)
                if(_cbnc_existing_header STREQUAL "${VERSION_HEADER_CONTENT}")
                    set(_cbnc_write_header FALSE)
                endif()
            endif()
            if(_cbnc_write_header)
                file(WRITE "${_CBNC_VERSION_HEADER}" "${VERSION_HEADER_CONTENT}")
                message(STATUS "[CBNC] Generated version header: ${_CBNC_VERSION_HEADER}")
            endif()

            # Create INTERFACE library for easy consumption via target_link_libraries
            if(NOT TARGET cbnc_version)
                add_library(cbnc_version INTERFACE)
                add_library(cbnc::version ALIAS cbnc_version)
            endif()
            target_include_directories(cbnc_version INTERFACE "${CMAKE_BINARY_DIR}/cbnc-generated")
        endif()

        return()
    endif()

    # --- BUILD mode (original behavior) ---

    # Require PROJECT_VERSION (already validated above)
    set(VERSION_MAJOR ${PROJECT_VERSION_MAJOR})
    set(VERSION_MINOR ${PROJECT_VERSION_MINOR})
    set(VERSION_PATCH ${PROJECT_VERSION_PATCH})

    # Create a CMake script that will run at build time
    set(GENERATOR_SCRIPT "${CMAKE_BINARY_DIR}/_cbnc_generate_${ARG_PROJECT_KEY}.cmake")

    # Helper: REUSE_COUNTER script body (read counter file + generate header idempotently)
    set(_CBNC_REUSE_SCRIPT "
set(LOCAL_FILE \"${ARG_LOCAL_FILE}\")
if(NOT EXISTS \"\${LOCAL_FILE}\")
    message(FATAL_ERROR \"REUSE_COUNTER requires an existing counter file, but '\${LOCAL_FILE}' does not exist.\")
endif()
file(READ \"\${LOCAL_FILE}\" BUILD_NUM_OUTPUT)
string(STRIP \"\${BUILD_NUM_OUTPUT}\" BUILD_NUM_OUTPUT)
if(NOT BUILD_NUM_OUTPUT MATCHES \"^[0-9]+$\")
    message(FATAL_ERROR \"Invalid content in counter file: '\${BUILD_NUM_OUTPUT}'\")
endif()

message(STATUS \"[CBNC] Build number for '${ARG_PROJECT_KEY}' (read-only): \${BUILD_NUM_OUTPUT}\")

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

# Idempotent write: skip if content unchanged (avoids recompilation)
set(_cbnc_write_header TRUE)
if(EXISTS \"${_CBNC_VERSION_HEADER}\")
    file(READ \"${_CBNC_VERSION_HEADER}\" _cbnc_existing_header)
    if(_cbnc_existing_header STREQUAL \"\${VERSION_HEADER_CONTENT}\")
        set(_cbnc_write_header FALSE)
    endif()
endif()
if(_cbnc_write_header)
    file(WRITE \"${_CBNC_VERSION_HEADER}\" \"\${VERSION_HEADER_CONTENT}\")
    message(STATUS \"[CBNC] Generated version header: ${_CBNC_VERSION_HEADER}\")
endif()
")

    # Build the skipped command string for DISABLED log messages
    set(_cbnc_skipped_cmd "${Python3_EXECUTABLE} ${CLIENT_SCRIPT} --project-key ${ARG_PROJECT_KEY} --local-file ${ARG_LOCAL_FILE} --output-format plain")
    if(ARG_SERVER_URL)
        string(APPEND _cbnc_skipped_cmd " --server-url ${ARG_SERVER_URL}")
    endif()
    if(ARG_SERVER_TOKEN)
        string(APPEND _cbnc_skipped_cmd " --server-token ***")
    endif()
    if(ARG_QUIET)
        string(APPEND _cbnc_skipped_cmd " --quiet")
    endif()

    if(ARG_DISABLED AND ARG_REUSE_COUNTER)
        # DISABLED + REUSE_COUNTER in BUILD mode: skip if header exists, else reuse counter
        file(WRITE "${GENERATOR_SCRIPT}" "
# Auto-generated script (DISABLED + REUSE_COUNTER)
if(EXISTS \"${_CBNC_VERSION_HEADER}\")
    message(STATUS \"[CBNC] Skipped (DISABLED): ${ARG_PROJECT_KEY}\")
else()
    ${_CBNC_REUSE_SCRIPT}
endif()
")

    elseif(ARG_DISABLED AND NOT ARG_FORCE_VERSION)
        # DISABLED alone in BUILD mode: skip/regenerate/bootstrap
        # Build the normal client.py script as a variable, then embed it in the else() branch
        set(_CBNC_CLIENT_SCRIPT_BODY "
# Build command arguments
set(CLIENT_ARGS
    \"${CLIENT_SCRIPT}\"
    --project-key \"${ARG_PROJECT_KEY}\"
    --local-file \"${ARG_LOCAL_FILE}\"
    --output-format plain")
        if(ARG_SERVER_URL)
            string(APPEND _CBNC_CLIENT_SCRIPT_BODY "\n    --server-url \"${ARG_SERVER_URL}\"")
        endif()
        if(ARG_SERVER_TOKEN)
            string(APPEND _CBNC_CLIENT_SCRIPT_BODY "\n    --server-token \"${ARG_SERVER_TOKEN}\"")
        endif()
        if(ARG_QUIET)
            string(APPEND _CBNC_CLIENT_SCRIPT_BODY "\n    --quiet")
        endif()
        string(APPEND _CBNC_CLIENT_SCRIPT_BODY "
)

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

# Print logs (one message per line for consistent -- prefix)
if(BUILD_NUM_ERROR)
    string(REPLACE \"\\n\" \";\" _cbnc_log_lines \"\${BUILD_NUM_ERROR}\")
    foreach(_cbnc_line IN LISTS _cbnc_log_lines)
        if(NOT _cbnc_line STREQUAL \"\")
            message(STATUS \"\${_cbnc_line}\")
        endif()
    endforeach()
endif()

# Validate output is a number
if(NOT BUILD_NUM_OUTPUT MATCHES \"^[0-9]+$\")
    message(FATAL_ERROR \"Invalid build number received: \${BUILD_NUM_OUTPUT}\")
endif()

message(STATUS \"[CBNC] Build number for '${ARG_PROJECT_KEY}': \${BUILD_NUM_OUTPUT}\")

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

# Idempotent write: skip if content unchanged (avoids recompilation)
set(_cbnc_write_header TRUE)
if(EXISTS \"${_CBNC_VERSION_HEADER}\")
    file(READ \"${_CBNC_VERSION_HEADER}\" _cbnc_existing_header)
    if(_cbnc_existing_header STREQUAL \"\${VERSION_HEADER_CONTENT}\")
        set(_cbnc_write_header FALSE)
    endif()
endif()
if(_cbnc_write_header)
    file(WRITE \"${_CBNC_VERSION_HEADER}\" \"\${VERSION_HEADER_CONTENT}\")
    message(STATUS \"[CBNC] Generated version header: ${_CBNC_VERSION_HEADER}\")
endif()
")

        file(WRITE "${GENERATOR_SCRIPT}" "
# Auto-generated script (DISABLED mode)
if(EXISTS \"${ARG_LOCAL_FILE}\" AND EXISTS \"${_CBNC_VERSION_HEADER}\")
    # Both exist — skip
    message(STATUS \"[CBNC] Skipped: ${_cbnc_skipped_cmd}\")
elseif(EXISTS \"${ARG_LOCAL_FILE}\")
    # Counter exists, header missing — regenerate
    ${_CBNC_REUSE_SCRIPT}
else()
    # Bootstrap: no files exist, run full increment
    ${_CBNC_CLIENT_SCRIPT_BODY}
endif()
")

    elseif(ARG_REUSE_COUNTER)
        # REUSE_COUNTER in BUILD mode: read from file instead of calling client.py
        file(WRITE "${GENERATOR_SCRIPT}" "
# Auto-generated script to read build number and generate version header (REUSE_COUNTER)
${_CBNC_REUSE_SCRIPT}
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

# Print logs (one message per line for consistent -- prefix)
if(BUILD_NUM_ERROR)
    string(REPLACE \"\\n\" \";\" _cbnc_log_lines \"\${BUILD_NUM_ERROR}\")
    foreach(_cbnc_line IN LISTS _cbnc_log_lines)
        if(NOT _cbnc_line STREQUAL \"\")
            message(STATUS \"\${_cbnc_line}\")
        endif()
    endforeach()
endif()

# Validate output is a number
if(NOT BUILD_NUM_OUTPUT MATCHES \"^[0-9]+$\")
    message(FATAL_ERROR \"Invalid build number received: \${BUILD_NUM_OUTPUT}\")
endif()

message(STATUS \"[CBNC] Build number for '${ARG_PROJECT_KEY}': \${BUILD_NUM_OUTPUT}\")

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

# Idempotent write: skip if content unchanged (avoids recompilation)
set(_cbnc_write_header TRUE)
if(EXISTS \"${_CBNC_VERSION_HEADER}\")
    file(READ \"${_CBNC_VERSION_HEADER}\" _cbnc_existing_header)
    if(_cbnc_existing_header STREQUAL \"\${VERSION_HEADER_CONTENT}\")
        set(_cbnc_write_header FALSE)
    endif()
endif()
if(_cbnc_write_header)
    file(WRITE \"${_CBNC_VERSION_HEADER}\" \"\${VERSION_HEADER_CONTENT}\")
    message(STATUS \"[CBNC] Generated version header: ${_CBNC_VERSION_HEADER}\")
endif()
")
    endif()

    # Create custom target that runs the generator script
    add_custom_target(${ARG_TARGET}
        ALL
        COMMAND ${CMAKE_COMMAND} -P "${GENERATOR_SCRIPT}"
        BYPRODUCTS "${_CBNC_VERSION_HEADER}"
        COMMENT "Incrementing build number for ${ARG_PROJECT_KEY}"
        VERBATIM
    )

    # Create INTERFACE library for easy consumption via target_link_libraries
    if(NOT TARGET cbnc_version)
        add_library(cbnc_version INTERFACE)
        add_library(cbnc::version ALIAS cbnc_version)
    endif()
    target_include_directories(cbnc_version INTERFACE "${CMAKE_BINARY_DIR}/cbnc-generated")
    add_dependencies(cbnc_version ${ARG_TARGET})

    message(STATUS "[CBNC] Build number will be incremented at build time for '${ARG_PROJECT_KEY}'")
    message(STATUS "[CBNC] Version header: ${_CBNC_VERSION_HEADER}")
    message(STATUS "[CBNC] Link with: target_link_libraries(<target> PRIVATE cbnc::version)")
endfunction()
