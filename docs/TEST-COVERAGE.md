# Test Coverage Map

Checklist of features and their test status. Update when adding new features or tests.

## CMake Module (`src/CMakeBuildNumber.cmake`)

| Feature | Status | Test File | Notes |
|---------|--------|-----------|-------|
| PROJECT_KEY validation | YES | test_cmake | `test_invalid_mode_fails` (indirect) |
| MODE=BUILD basic | YES | test_cmake | `test_example_simple_builds` |
| MODE=BUILD increments (2 builds) | YES | test_cmake | `test_build_number_increments` |
| MODE=BUILD no auto-reconfigure | YES | test_cmake | `test_build_mode_no_auto_reconfigure` |
| MODE=BUILD + REUSE_COUNTER | YES | test_cmake | `test_build_mode_reuse_counter` |
| MODE=BUILD + FORCE_VERSION | NO | — | Only tested in CONFIGURE mode |
| MODE=CONFIGURE basic | YES | test_cmake | `test_configure_mode_builds` |
| MODE=CONFIGURE increments (2 configures) | YES | test_cmake | `test_configure_mode_increments_on_reconfigure` |
| MODE=CONFIGURE auto-reconfigure on build | YES | test_cmake | `test_configure_mode_auto_reconfigure_on_build` |
| MODE=CONFIGURE + OUTPUT_VARIABLE before project() | YES | test_cmake | `test_configure_mode_output_variable_before_project` |
| MODE=CONFIGURE + auto-generated header after project() | YES | test_cmake | `test_configure_mode_version_header_after_project` |
| MODE=CONFIGURE + REUSE_COUNTER | YES | test_cmake | `test_reuse_counter_reads_current_value` |
| MODE=CONFIGURE + FORCE_VERSION | YES | test_cmake | `test_configure_mode_force_version` |
| REUSE_COUNTER without counter file | YES | test_cmake | `test_reuse_counter_fails_without_prior_counter` |
| REUSE_COUNTER + FORCE_VERSION (mutual exclusion) | YES | test_cmake | `test_reuse_counter_with_force_version_fails` |
| REUSE_COUNTER ignores server changes | YES | test_cmake | `test_reuse_counter_ignores_server_changes` |
| DISABLED bootstrap (CONFIGURE, first run) | YES | test_cmake | `test_disabled_bootstrap_first_run` |
| DISABLED skips when files exist (CONFIGURE) | YES | test_cmake | `test_disabled_skips_when_files_exist` |
| DISABLED regenerates header when missing | YES | test_cmake | `test_disabled_regenerates_header_when_missing` |
| DISABLED no stamp file | YES | test_cmake | `test_disabled_no_stamp_file` |
| DISABLED + FORCE_VERSION | YES | test_cmake | `test_disabled_with_force_version` |
| DISABLED + REUSE_COUNTER header regen | YES | test_cmake | `test_disabled_reuse_counter_regenerates_header` |
| DISABLED + REUSE_COUNTER + FORCE_VERSION (mutual exclusion) | YES | test_cmake | `test_disabled_reuse_counter_force_version_fatal_error` |
| DISABLED skip log contains full command | YES | test_cmake | `test_disabled_skipped_log_contains_full_command` |
| DISABLED sequence (DISABLED→normal→DISABLED) | YES | test_cmake | `test_disabled_sequence_disabled_normal_disabled` |
| DISABLED bootstrap (BUILD mode) | YES | test_cmake | `test_build_mode_disabled_bootstrap` |
| DISABLED skips (BUILD mode) | YES | test_cmake | `test_build_mode_disabled_skips` |
| FORCE_VERSION idempotent header (CONFIGURE) | YES | test_cmake | `test_force_version_idempotent_header` |
| FORCE_VERSION idempotent header (BUILD) | YES | test_cmake | `test_force_version_idempotent_header_build_mode` |
| FORCE_VERSION idempotent counter file | YES | test_client | `test_force_set_idempotent_counter_file` |
| Invalid MODE value | YES | test_cmake | `test_invalid_mode_fails` |
| FetchContent (BUILD mode) | YES | test_cmake | `test_fetchcontent_build_mode` |
| FetchContent (CONFIGURE mode) | YES | test_cmake | `test_fetchcontent_configure_mode` |
| SERVER_URL via CMake param (BUILD) | YES | test_cmake | `test_server_build_mode_cmake_params` |
| SERVER_URL via CMake param (CONFIGURE) | YES | test_cmake | `test_server_configure_mode_cmake_params` |
| SERVER_URL via env var (BUILD) | YES | test_cmake | `test_server_build_mode_env_vars` |
| SERVER_URL via env var (CONFIGURE) | YES | test_cmake | `test_server_configure_mode_env_vars` |
| SERVER_TOKEN via CMake param | YES | test_cmake | `test_server_auth_project_token` |
| SERVER_TOKEN via env var | YES | test_cmake | `test_server_auth_env_var_token` |
| Auth: project-scoped token | YES | test_cmake | `test_server_auth_project_token` |
| Auth: wildcard token | YES | test_cmake | `test_server_auth_wildcard_token` |
| Auth: admin token | YES | test_cmake | `test_server_auth_admin_token` |
| Auth: wrong token → build fails | YES | test_cmake | `test_server_auth_wrong_token_fails_build` |
| Auth: wrong project → build fails | YES | test_cmake | `test_server_auth_wrong_project_fails_build` |
| Manual include/dependency (legacy) | YES | test_cmake | `test_manual_include_and_dependency` |
| Custom TARGET name | YES | test_cmake | `test_manual_include_and_dependency` |
| QUIET flag suppresses info, warnings still print | YES | test_client::TestLogMessages, test_integration | Channel separation: `log_message` (info) suppressed, `log_warning` always prints |
| Custom LOCAL_FILE path | NO | — | Uses default in all tests |

## Client (`src/client.py`)

| Feature | Status | Test File | Notes |
|---------|--------|-----------|-------|
| Local increment (no server) | YES | test_client | `TestIncrementLocally`, `TestGetBuildNumber` |
| Server increment | YES | test_client | `TestIncrementOnServer` (mocked) |
| Server unavailable → local fallback | YES | test_client, test_integration | |
| Server rejection (401/403/429) → error | YES | test_client, test_integration | `TestServerRejection` |
| Server error (500) → local fallback | YES | test_client | `test_server_error_500_returns_none` |
| Sync state (.sync file) | YES | test_client | `TestSyncState` |
| Offline → online sync | YES | test_integration | `TestClientServerIntegration`, `TestOnlineOfflineOnline` |
| Online → offline → online (continuous) | YES | test_integration | `TestOnlineOfflineOnline` |
| Online → offline → online (server advanced) | YES | test_integration | `TestOnlineOfflineOnline` |
| Force-version (local) | YES | test_client, test_integration | `TestForceSetBuildNumber`, `TestClientForceVersion` |
| Force-version (server) | YES | test_integration | `TestClientForceVersion` |
| Output formats (plain/cmake/json) | YES | test_client, test_integration | `TestFormatOutput` |
| Project key validation | YES | test_client | `TestProjectKeyValidation` |
| --server-token header | YES | test_client | `TestIncrementOnServerAuth`, `TestSetOnServer` |
| BUILD_SERVER_URL env var | PARTIAL | test_cmake | Tested via CMake, not directly |
| BUILD_SERVER_TOKEN env var | PARTIAL | test_cmake | Tested via CMake, not directly |
| Corrupted local counter file | YES | test_client | `TestLoadLocalCounter` (corrupt) |
| Corrupted .sync file | NO | — | |
| Negative --force-version | NO | — | Client validates but no test |

## Server (`src/server.py`)

| Feature | Status | Test File | Notes |
|---------|--------|-----------|-------|
| GET / (service info) | YES | test_server | `TestServerGET` |
| POST /increment | YES | test_server | `TestServerIncrement` |
| POST /set | YES | test_server | `TestSetEndpoint` |
| Local version sync | YES | test_server | `TestServerLocalVersionSync` |
| Project approval (--accept-unknown) | YES | test_server | `TestServerApproval` |
| Max projects limit | YES | test_server | `TestMaxProjectLimit` |
| Body size limit | YES | test_server | `TestContentLengthLimit` |
| Project key validation | YES | test_server | `TestServerProjectKeyValidation` |
| Token auth (project-scoped) | YES | test_server | `TestServerAuth` |
| Token auth (admin) | YES | test_server | `TestServerAuth` |
| Token auth (wildcard) | YES | test_server | `TestServerAuth` |
| Token CLI (add/remove/list) | YES | test_server | `TestTokenCLI` |
| Rate limiting | YES | test_server | `TestRateLimiting` |
| Temporary bans | YES | test_server | `TestRateLimiting` |
| Permanent bans | YES | test_server | `TestRateLimiting` |
| Client IP from trusted proxy header | YES | test_server | `TestResolveOrigin` |
| Per-client rate limit behind trusted proxy | YES | test_server, test_integration | `TestTrustedProxyServer`, `TestTrustedProxyIntegration` |
| Untrusted peer cannot spoof client IP | YES | test_server | `TestTrustedProxyServer.test_untrusted_peer_cannot_spoof_header` |
| Unusable header from trusted proxy falls back to peer | YES | test_server | `TestTrustedProxyServer.test_trusted_proxy_without_usable_header_limits_by_peer` |
| Repeated client-IP header lines | YES | test_server | `TestResolveOrigin.test_repeated_header_lines_use_last_entry` |
| /healthz forwarded by loopback proxy is public | YES | test_server | `TestTrustedProxyServer.test_healthz_forwarded_by_loopback_proxy_is_public` |
| /healthz from loopback without forwarding stays local | YES | test_server | `TestTrustedProxyServer.test_healthz_from_loopback_proxy_without_header_is_local`, `TestTrustedProxyServer.test_healthz_header_from_untrusted_loopback_peer_is_ignored` |
| HTTP/0.9 request from trusted proxy | YES | test_server | `TestTrustedProxyServer.test_origin_without_message_headers_is_direct`, `TestTrustedProxyServer.test_http09_request_from_trusted_proxy_is_served` (Python 3.13+) |
| /healthz local for IPv6 loopback peers | YES | test_server | `TestTrustedProxyServer.test_healthz_from_ipv6_loopback_peer_is_local`, `TestTrustedProxyServer.test_healthz_from_public_or_forwarded_ipv6_peer_is_public` |
| Permanent ban behind trusted proxy records client IP | YES | test_server | `TestTrustedProxyServer.test_permanent_ban_records_forwarded_client` |
| Custom --real-ip-header | YES | test_integration | `TestTrustedProxyIntegration.test_custom_real_ip_header` |
| IPv6 and IPv4 --trusted-proxy accepted | YES | test_integration | `TestTrustedProxyIntegration.test_ipv6_and_ipv4_trusted_proxies_are_accepted` |
| Invalid --trusted-proxy / --real-ip-header rejected | YES | test_integration | `TestTrustedProxyIntegration.test_invalid_proxy_options_are_rejected` |
| --set-counter CLI | YES | test_integration | `TestServerSetCounter` |
| Corrupted build_numbers.json | NO | — | |
| Corrupted banned_ips.json | NO | — | |
| Concurrent increments (race condition) | YES | test_server | `TestPooledHTTPServer.test_concurrent_increments_no_lost_updates` |
| TOCTOU fix (discriminated result) | YES | test_server | `TestTOCTOUAndDiscriminatedResults` |
| Concurrent project limit enforcement | YES | test_server | `TestTOCTOUAndDiscriminatedResults.test_concurrent_new_projects_limit_respected` |
| PooledHTTPServer worker pool | YES | test_server | `TestPooledHTTPServer` |
| 503 Service Unavailable on overload | YES | test_server | `TestPooledHTTPServer.test_503_when_queue_full` |
| Daemon worker threads | YES | test_server | `TestPooledHTTPServer.test_workers_are_daemon` |
| Server close drains queue | YES | test_server | `TestPooledHTTPServer.test_server_close_idempotent` |
| Socket timeout (Slowloris defense) | YES | test_server | `TestSocketTimeout` |
| 408 Request Timeout on partial body | YES | test_server | `TestSocketTimeout.test_partial_body_returns_408` |
| Tokens cache with mtime invalidation | YES | test_server | `TestTokensCache` |
| Tokens cache concurrent reads | YES | test_server | `TestTokensCache.test_concurrent_cache_reads_no_corruption` |
| GET /healthz endpoint | YES | test_server | `TestHealthz` |
| /healthz bypasses rate limit for local callers | YES | test_server | `TestHealthz.test_healthz_bypasses_rate_limit` |
| Watchdog os._exit on failures | YES | test_server | `TestWatchdog` |
| Watchdog recovers from intermittent failures | YES | test_server | `TestWatchdog.test_recovers_after_intermittent_failure` |

## Docker (`Dockerfile`)

Tests gated by `CBNC_TEST_DOCKER=1`, marked `@pytest.mark.docker`.

| Feature | Status | Test File | Notes |
|---------|--------|-----------|-------|
| Container starts and responds (GET /) | YES | test_docker | `test_root_endpoint` |
| Increment via container | YES | test_docker | `test_increment` |
| Data persisted to volume | YES | test_docker | `test_data_persisted_to_volume` |
| Restart preserves data | YES | test_docker | `test_restart_preserves_data` |
| Custom --port flag | YES | test_docker | `test_custom_port` |
| Token auth via container | NO | — | |

## Cross-Feature Integration

| Feature | Status | Test File | Notes |
|---------|--------|-----------|-------|
| CMake → client → server (BUILD mode) | YES | test_cmake | `test_server_build_mode_cmake_params` |
| CMake → client → server (CONFIGURE mode) | YES | test_cmake | `test_server_configure_mode_cmake_params` |
| CMake → client → server with auth | YES | test_cmake | `test_server_auth_*` (5 tests) |
| CMake → client → server rejection → build fails | YES | test_cmake | `test_server_auth_wrong_*_fails_build` (2 tests) |
| Client → server → offline → online sync | YES | test_integration | `TestClientServerIntegration`, `TestOnlineOfflineOnline` |
| CMake + server + rate limiting | NO | — | |
| CMake + multiple features combined | NO | — | |
