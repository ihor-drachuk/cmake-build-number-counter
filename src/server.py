#!/usr/bin/env python3
"""
Build Number Counter Server

A simple HTTP server that manages build numbers for multiple projects.
Provides atomic increment operations with persistent storage.
"""

import fnmatch
import http.client
import json
import argparse
import os
import queue
import secrets
import socket
import sys
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from urllib.parse import urlparse
from validation import validate_project_key


DEFAULT_MAX_WORKERS = 64

# Network errors we silence in handle_error: client disconnects and idle
# timeouts are normal traffic, not server bugs. Real exceptions still
# surface through the standard handle_error path.
_BENIGN_NETWORK_ERRORS = (ConnectionError, socket.timeout, TimeoutError)


class PooledHTTPServer(HTTPServer):
    """HTTP server with a fixed worker thread pool and bounded queue.

    Architecture: serve_forever() runs in the main thread and only does
    accept() + push the socket into a bounded Queue. N daemon worker
    threads pull from the Queue and run finish_request().

    Hard cap on concurrent connections: max_workers in flight + max_workers
    queued. Excess clients receive 503 Service Unavailable immediately
    (best-effort raw write, then close). Workers are daemon threads — they
    die with the process; we do not join them on shutdown to keep teardown
    fast and to avoid hanging on a stuck request.
    """

    def __init__(self, server_address, RequestHandlerClass,
                 bind_and_activate=True, max_workers=None):
        super().__init__(server_address, RequestHandlerClass, bind_and_activate)
        self._max_workers = max_workers or DEFAULT_MAX_WORKERS
        self._work_queue = queue.Queue(maxsize=self._max_workers)
        self._shutdown_event = threading.Event()
        self._workers = []
        # Update process-wide start time so /healthz reports uptime
        # relative to the most recent server instance (in tests there
        # may be many sequential instances on different ports).
        global _server_start_time
        _server_start_time = time.monotonic()
        for i in range(self._max_workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f'cbnc-worker-{i}',
                daemon=True,
            )
            t.start()
            self._workers.append(t)

    def process_request(self, request, client_address):
        """Hand the socket off to a worker, or refuse with 503."""
        try:
            self._work_queue.put_nowait((request, client_address))
        except queue.Full:
            self._refuse_overloaded(request)

    def handle_error(self, request, client_address):
        exc_type = sys.exc_info()[0]
        if exc_type is not None and issubclass(exc_type, _BENIGN_NETWORK_ERRORS):
            return
        super().handle_error(request, client_address)

    def server_close(self):
        """Close listening socket, signal workers, drain pending sockets."""
        super().server_close()
        self._shutdown_event.set()
        # Drain any sockets still in the queue — leaving them open would
        # leak file descriptors, especially in the test suite.
        while True:
            try:
                request, _ = self._work_queue.get_nowait()
            except queue.Empty:
                break
            try:
                self.shutdown_request(request)
            except OSError:
                pass
        # Workers exit on the next Queue.get(timeout=0.5) tick. We do not
        # join them: they're daemon and a stuck handler must not block
        # shutdown.

    def _worker_loop(self):
        while not self._shutdown_event.is_set():
            try:
                item = self._work_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            request, client_address = item
            try:
                self.finish_request(request, client_address)
            except Exception:
                self.handle_error(request, client_address)
            finally:
                try:
                    self.shutdown_request(request)
                except OSError:
                    pass

    _OVERLOADED_BODY = b'{"error":"Server overloaded, try again later"}'
    _OVERLOADED_RESPONSE = (
        b"HTTP/1.1 503 Service Unavailable\r\n"
        b"Content-Type: application/json\r\n"
        b"Connection: close\r\n"
        b"Content-Length: " + str(len(_OVERLOADED_BODY)).encode('ascii') + b"\r\n"
        b"\r\n"
        + _OVERLOADED_BODY
    )

    def _refuse_overloaded(self, request):
        """Best-effort 503 then close. Runs in the accept (main) thread."""
        # Short send timeout so a slow consumer cannot stall the accept loop.
        try:
            request.settimeout(0.1)
            request.sendall(self._OVERLOADED_RESPONSE)
        except (OSError, socket.timeout, TimeoutError):
            pass
        finally:
            try:
                self.shutdown_request(request)
            except OSError:
                pass


# Backward-compat alias: older test fixtures and external code may
# import QuietHTTPServer by name.
class QuietHTTPServer(PooledHTTPServer):
    pass

# Global state (initialized in main() or by test fixtures)
DATA_DIR = None
BUILD_NUMBERS_FILE = None
file_lock = threading.Lock()
accept_unknown = False
max_body_size = 1024
max_projects = 100
TOKENS_FILE = None

# Rate limiting state
rate_limit = 10               # max requests per minute per IP; 0 = disabled
ban_duration = 600            # seconds for temporary ban
ban_permanent = False         # if True, bans persist to banned_ips.json

rate_tracker = {}             # {ip: [monotonic_timestamps]}
temp_bans = {}                # {ip: monotonic_expiry}
permanent_bans = set()        # in-memory cache of banned_ips.json
permanent_bans_mtime = 0.0    # last known mtime of banned_ips.json
rate_lock = threading.Lock()  # separate from file_lock

# Tokens cache (mtime-invalidated). Keeps load_tokens() off the hot disk
# I/O path on every authenticated request. Sentinel mtime -1.0 means
# "cache empty / file absent on last check".
_tokens_cache = {}
_tokens_cache_mtime = -1.0
_tokens_cache_lock = threading.Lock()

# Server lifecycle: monotonic timestamp when serve_forever() started.
# Read by GET /healthz to report uptime.
_server_start_time = 0.0


class _BodyTooLarge(Exception):
    def __init__(self, size):
        self.size = size


def load_json_file(filename, default):
    """Load JSON file with error handling."""
    if not os.path.exists(filename):
        return default
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Error reading {filename}: {e}", file=sys.stderr)
        return default


def save_json_file(filename, data):
    """Save JSON file atomically."""
    temp_file = filename + ".tmp"
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    # Atomic replace
    os.replace(temp_file, filename)


def is_project_approved(project_key):
    """
    Check if project key is approved.
    A project is considered approved if it already exists in build_numbers.json.
    """
    build_numbers = load_json_file(BUILD_NUMBERS_FILE, {})
    return project_key in build_numbers


def is_project_limit_reached():
    """Check whether the project count has reached the configured maximum."""
    if max_projects == 0:
        return False
    build_numbers = load_json_file(BUILD_NUMBERS_FILE, {})
    return len(build_numbers) >= max_projects


def load_tokens():
    """Load tokens with mtime-based caching. Thread-safe.

    Returns the in-memory tokens dict (token_value -> metadata) without
    touching the disk when tokens.json mtime has not changed since the
    last successful load.
    """
    global _tokens_cache, _tokens_cache_mtime

    if TOKENS_FILE is None or not os.path.exists(TOKENS_FILE):
        with _tokens_cache_lock:
            if _tokens_cache_mtime != -1.0:
                _tokens_cache = {}
                _tokens_cache_mtime = -1.0
            return _tokens_cache

    try:
        mtime = os.path.getmtime(TOKENS_FILE)
    except OSError:
        with _tokens_cache_lock:
            return _tokens_cache

    with _tokens_cache_lock:
        if mtime != _tokens_cache_mtime:
            data = load_json_file(TOKENS_FILE, {})
            _tokens_cache = data.get("tokens", {})
            _tokens_cache_mtime = mtime
        return _tokens_cache


def authenticate_request(handler, project_key):
    """
    Validate Authorization header against tokens.json.

    Returns:
        (True, None) if auth is disabled or token is valid for this project.
        (False, error_message) if token is missing, invalid, or lacks access.
    """
    tokens = load_tokens()

    if not tokens:
        return True, None  # Auth disabled

    auth_header = handler.headers.get('Authorization', '')

    if not auth_header.startswith('Bearer '):
        return False, 'Missing or malformed Authorization header. Expected: Bearer <token>'

    token_value = auth_header[len('Bearer '):]

    if token_value not in tokens:
        return False, 'Invalid token'

    token_meta = tokens[token_value]

    if token_meta.get('admin', False):
        return True, None

    for pattern in token_meta.get('projects', []):
        if fnmatch.fnmatch(project_key, pattern):
            return True, None

    return False, f'Token does not have access to project "{project_key}"'


def check_rate_limit(handler):
    """Check rate limit for the client IP.

    Returns True if request is allowed, False if rejected (429 sent).
    """
    if rate_limit <= 0:
        return True

    ip = handler.client_address[0]
    now = time.monotonic()

    with rate_lock:
        # 1. Check permanent ban
        if ban_permanent and _is_permanently_banned(ip):
            handler.send_json_response(429, {
                'error': 'Permanently banned due to rate limit violation',
                'ban_type': 'permanent',
                'ip': ip,
            })
            return False

        # 2. Check temporary ban
        if not ban_permanent and ip in temp_bans:
            expiry = temp_bans[ip]
            if now < expiry:
                remaining = int(expiry - now)
                handler.send_json_response(429, {
                    'error': 'Temporarily banned due to rate limit violation',
                    'ban_type': 'temporary',
                    'retry_after_seconds': remaining,
                    'ip': ip,
                })
                return False
            else:
                del temp_bans[ip]

        # 3. Sliding window check
        timestamps = rate_tracker.get(ip, [])
        cutoff = now - 60.0
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)

        if len(timestamps) >= rate_limit:
            _ban_ip(ip, now)
            if ban_permanent:
                handler.send_json_response(429, {
                    'error': 'Permanently banned due to rate limit violation',
                    'ban_type': 'permanent',
                    'ip': ip,
                })
            else:
                handler.send_json_response(429, {
                    'error': 'Temporarily banned due to rate limit violation',
                    'ban_type': 'temporary',
                    'retry_after_seconds': ban_duration,
                    'ip': ip,
                })
            return False

        # 4. Record this request
        timestamps.append(now)
        rate_tracker[ip] = timestamps

    return True


def _ban_ip(ip, now):
    """Ban an IP address. Called under rate_lock."""
    if ban_permanent:
        permanent_bans.add(ip)
        _save_permanent_bans(ip)
        print(f"Permanently banned IP: {ip}")
    else:
        temp_bans[ip] = now + ban_duration
        print(f"Temporarily banned IP: {ip} for {ban_duration}s")
    rate_tracker.pop(ip, None)


def _is_permanently_banned(ip):
    """Check if IP is permanently banned. Refreshes cache if file changed."""
    global permanent_bans, permanent_bans_mtime

    if ip in permanent_bans:
        return True

    ban_file = os.path.join(DATA_DIR, "banned_ips.json")
    try:
        mtime = os.path.getmtime(ban_file)
        if mtime != permanent_bans_mtime:
            data = load_json_file(ban_file, {"banned": {}})
            permanent_bans = set(data.get("banned", {}).keys())
            permanent_bans_mtime = mtime
    except OSError:
        pass

    return ip in permanent_bans


def _save_permanent_bans(ip):
    """Save permanent ban to file. Called under rate_lock."""
    global permanent_bans_mtime
    ban_file = os.path.join(DATA_DIR, "banned_ips.json")
    data = load_json_file(ban_file, {"banned": {}})
    data["banned"][ip] = {
        "banned_at": datetime.now(timezone.utc).isoformat(),
        "reason": f"Rate limit exceeded ({rate_limit} req/min)",
    }
    save_json_file(ban_file, data)
    try:
        permanent_bans_mtime = os.path.getmtime(ban_file)
    except OSError:
        pass


def cleanup_rate_data():
    """Remove stale entries from rate_tracker and expired temp bans."""
    with rate_lock:
        now = time.monotonic()
        cutoff = now - 60.0

        stale_ips = [
            ip for ip, ts in rate_tracker.items()
            if not ts or ts[-1] < cutoff
        ]
        for ip in stale_ips:
            del rate_tracker[ip]

        expired = [
            ip for ip, expiry in temp_bans.items()
            if now >= expiry
        ]
        for ip in expired:
            del temp_bans[ip]


def _start_cleanup_timer():
    """Start periodic cleanup of rate limiting data."""
    cleanup_rate_data()
    timer = threading.Timer(60.0, _start_cleanup_timer)
    timer.daemon = True
    timer.start()


def validate_local_version(value):
    """Validate local_version parameter.

    Returns:
        (True, None) if valid, (False, error_message) if invalid.
    """
    if value is None:
        return True, None
    if isinstance(value, bool) or not isinstance(value, int):
        return False, "local_version must be an integer"
    if value < 0:
        return False, "local_version must be >= 0"
    return True, None


def validate_version(value):
    """Validate version parameter for force-set.

    Returns:
        (True, None) if valid, (False, error_message) if invalid.
    """
    if value is None:
        return False, "Missing version parameter"
    if isinstance(value, bool) or not isinstance(value, int):
        return False, "version must be an integer"
    if value < 0:
        return False, "version must be >= 0"
    return True, None


@dataclass
class _IncrementResult:
    status: str  # 'ok' | 'unapproved' | 'limit_reached'
    build_number: Optional[int] = None


@dataclass
class _SetResult:
    status: str  # 'ok' | 'unapproved'
    build_number: Optional[int] = None


def increment_build_number(project_key, local_version=None):
    """Atomically check approval/limit and increment counter.

    All approval/limit checks and the increment happen within a single
    file_lock critical section to avoid TOCTOU under multi-threading.

    Returns:
        _IncrementResult with status:
            'ok'             -> build_number is the new value
            'unapproved'     -> project is unknown and accept_unknown is False
            'limit_reached'  -> new project would exceed max_projects
    """
    with file_lock:
        build_numbers = load_json_file(BUILD_NUMBERS_FILE, {})
        is_new = project_key not in build_numbers

        if is_new and not accept_unknown:
            return _IncrementResult(status='unapproved')

        if is_new and max_projects > 0 and len(build_numbers) >= max_projects:
            return _IncrementResult(status='limit_reached')

        current = build_numbers.get(project_key, 0)
        if local_version is not None and local_version > current:
            current = local_version
            print(f"Updated {project_key} from local version: {local_version}")

        new_number = current + 1
        build_numbers[project_key] = new_number
        save_json_file(BUILD_NUMBERS_FILE, build_numbers)

        print(f"Incremented {project_key}: {current} -> {new_number}")
        return _IncrementResult(status='ok', build_number=new_number)


def set_build_number(project_key, version, force_unapproved=False):
    """Atomically check approval and set counter to an exact value.

    Args:
        project_key: Project identifier (already validated by caller).
        version: Non-negative integer to set the counter to.
        force_unapproved: If True, bypass the approval check. Used by the
            CLI --set-counter command, which is a local admin operation
            that must work regardless of accept_unknown.

    Returns:
        _SetResult with status:
            'ok'         -> build_number is the value that was written
            'unapproved' -> project is unknown and (accept_unknown is False
                            and force_unapproved is False)

    Raises:
        ValueError: If version is invalid (negative, non-int, or bool).
    """
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise ValueError(f"version must be a non-negative integer, got {version!r}")

    with file_lock:
        build_numbers = load_json_file(BUILD_NUMBERS_FILE, {})
        is_new = project_key not in build_numbers

        if is_new and not accept_unknown and not force_unapproved:
            return _SetResult(status='unapproved')

        build_numbers[project_key] = version
        save_json_file(BUILD_NUMBERS_FILE, build_numbers)
        print(f"Set {project_key} to {version}")
        return _SetResult(status='ok', build_number=version)


class BuildNumberHandler(BaseHTTPRequestHandler):
    """HTTP request handler for build number operations."""

    # StreamRequestHandler.setup() applies this to self.connection:
    # self.rfile.read() and self.wfile.write() will raise socket.timeout
    # after this many seconds of socket inactivity. Defends against
    # Slowloris-style clients that send headers and then stall on body.
    timeout = 3

    def log_message(self, format, *args):
        """Override to customize logging."""
        sys.stdout.write(f"[{self.log_date_time_string()}] {format % args}\n")

    def send_json_response(self, status_code, data):
        """Send JSON response."""
        self.send_response(status_code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def read_body(self):
        """
        Read and return the request body, enforcing the size limit.

        Returns:
            The body as a str.

        Raises:
            _BodyTooLarge: if the body exceeds max_body_size.
        """
        content_length_header = self.headers.get('Content-Length')
        if content_length_header is not None:
            try:
                declared_size = int(content_length_header)
            except ValueError:
                declared_size = 0
            if declared_size < 0 or declared_size > max_body_size:
                raise _BodyTooLarge(declared_size)
            read_size = declared_size
        else:
            read_size = max_body_size + 1

        raw = self.rfile.read(min(read_size, max_body_size + 1))
        if len(raw) > max_body_size:
            raise _BodyTooLarge(len(raw))

        return raw.decode('utf-8')

    def do_POST(self):
        """Handle POST requests."""
        if not check_rate_limit(self):
            return
        try:
            body = self.read_body()
        except _BodyTooLarge as e:
            self.send_json_response(413, {
                'error': 'Request body too large',
                'max_bytes': max_body_size,
                'received_bytes': e.size,
            })
            return
        except (socket.timeout, TimeoutError):
            try:
                self.send_json_response(408, {
                    'error': 'Request timeout while reading body'
                })
            except (OSError, socket.timeout, TimeoutError, BrokenPipeError):
                pass  # socket already gone — abort silently
            return

        parsed_path = urlparse(self.path)

        if parsed_path.path == '/increment':
            try:
                data = json.loads(body) if body else {}

                project_key = data.get('project_key')
                local_version = data.get('local_version')

                if not project_key:
                    self.send_json_response(400, {
                        'error': 'Missing project_key parameter'
                    })
                    return

                if local_version is not None:
                    valid, error_msg = validate_local_version(local_version)
                    if not valid:
                        self.send_json_response(400, {
                            'error': error_msg,
                            'field': 'local_version',
                            'value': str(local_version)
                        })
                        return

                try:
                    validate_project_key(project_key)
                except ValueError as e:
                    self.send_json_response(400, {'error': str(e)})
                    return

                auth_ok, auth_error = authenticate_request(self, project_key)
                if not auth_ok:
                    self.send_json_response(401, {'error': auth_error})
                    return

                result = increment_build_number(project_key, local_version)

                if result.status == 'unapproved':
                    self.send_json_response(403, {
                        'error': f'Project key "{project_key}" is not approved. Add it to build_numbers.json or restart server with --accept-unknown',
                        'project_key': project_key
                    })
                elif result.status == 'limit_reached':
                    self.send_json_response(507, {
                        'error': 'Maximum project limit reached',
                        'detail': f'Server is configured to allow at most {max_projects} projects. '
                                  'Contact the server administrator to increase the limit '
                                  'or remove unused projects.',
                        'max_projects': max_projects,
                        'project_key': project_key,
                    })
                elif result.status == 'ok':
                    self.send_json_response(200, {
                        'build_number': result.build_number,
                        'project_key': project_key
                    })
                else:
                    self.send_json_response(500, {
                        'error': f'Unexpected increment status: {result.status}'
                    })

            except json.JSONDecodeError:
                self.send_json_response(400, {
                    'error': 'Invalid JSON in request body'
                })
            except Exception as e:
                print(f"Error processing request: {e}", file=sys.stderr)
                self.send_json_response(500, {
                    'error': str(e)
                })
        elif parsed_path.path == '/set':
            try:
                data = json.loads(body) if body else {}

                project_key = data.get('project_key')
                version = data.get('version')

                if not project_key:
                    self.send_json_response(400, {
                        'error': 'Missing project_key parameter'
                    })
                    return

                try:
                    validate_project_key(project_key)
                except ValueError as e:
                    self.send_json_response(400, {'error': str(e)})
                    return

                valid, error_msg = validate_version(version)
                if not valid:
                    self.send_json_response(400, {
                        'error': error_msg,
                        'field': 'version',
                        'value': str(version)
                    })
                    return

                auth_ok, auth_error = authenticate_request(self, project_key)
                if not auth_ok:
                    self.send_json_response(401, {'error': auth_error})
                    return

                result = set_build_number(project_key, version)

                if result.status == 'unapproved':
                    self.send_json_response(403, {
                        'error': f'Project key "{project_key}" is not approved',
                        'project_key': project_key
                    })
                elif result.status == 'ok':
                    self.send_json_response(200, {
                        'build_number': result.build_number,
                        'project_key': project_key
                    })
                else:
                    self.send_json_response(500, {
                        'error': f'Unexpected set status: {result.status}'
                    })

            except json.JSONDecodeError:
                self.send_json_response(400, {
                    'error': 'Invalid JSON in request body'
                })
            except Exception as e:
                print(f"Error processing /set request: {e}", file=sys.stderr)
                self.send_json_response(500, {
                    'error': str(e)
                })
        else:
            self.send_json_response(404, {
                'error': 'Not found',
                'available_endpoints': ['/increment (POST)', '/set (POST)']
            })

    def do_GET(self):
        """Handle GET requests (status/info only)."""
        parsed_path = urlparse(self.path)

        # /healthz bypasses rate limit so the watchdog (or external probe)
        # cannot accidentally ban itself, and stays cheap to call.
        if parsed_path.path == '/healthz':
            try:
                self.send_json_response(200, {
                    'status': 'ok',
                    'workers': self.server._max_workers,
                    'queue_depth': self.server._work_queue.qsize(),
                    'uptime_seconds': int(time.monotonic() - _server_start_time),
                })
            except (OSError, socket.timeout, TimeoutError, BrokenPipeError):
                pass
            return

        if not check_rate_limit(self):
            return

        if parsed_path.path == '/':
            self.send_json_response(200, {
                'service': 'Build Number Counter Server',
                'endpoints': {
                    '/increment': 'POST with JSON body: {"project_key": "key", "local_version": N (optional)}',
                    '/set': 'POST with JSON body: {"project_key": "key", "version": N}',
                    '/healthz': 'GET — liveness probe (no auth, no rate limit)'
                }
            })
        else:
            self.send_json_response(404, {
                'error': 'Not found'
            })


def init_data_dir(data_dir):
    """Initialize data directory, file paths, and reset module caches."""
    global DATA_DIR, BUILD_NUMBERS_FILE, TOKENS_FILE
    global _tokens_cache, _tokens_cache_mtime
    DATA_DIR = data_dir
    BUILD_NUMBERS_FILE = os.path.join(DATA_DIR, "build_numbers.json")
    TOKENS_FILE = os.path.join(DATA_DIR, "tokens.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    # Caches are file-path-bound; reset whenever paths change (e.g. test
    # fixtures that share the module across multiple tmp_path dirs).
    with _tokens_cache_lock:
        _tokens_cache = {}
        _tokens_cache_mtime = -1.0


def _handle_add_token(args):
    """Create a new API token and write it to tokens.json."""
    if not args.token_name:
        print("Error: --token-name is required with --add-token", file=sys.stderr)
        sys.exit(1)
    if not args.token_projects and not args.token_admin:
        print("Error: --token-projects or --token-admin is required with --add-token", file=sys.stderr)
        sys.exit(1)

    data = load_json_file(TOKENS_FILE, {})
    tokens = data.get("tokens", {})

    # Check for duplicate name
    for meta in tokens.values():
        if meta.get("name") == args.token_name:
            print(f"Error: Token with name '{args.token_name}' already exists", file=sys.stderr)
            sys.exit(1)

    token_value = secrets.token_hex(32)
    projects = [p.strip() for p in args.token_projects.split(",")] if args.token_projects else []

    tokens[token_value] = {
        "name": args.token_name,
        "projects": projects,
        "admin": args.token_admin,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    data["tokens"] = tokens
    save_json_file(TOKENS_FILE, data)

    access = "(all)" if args.token_admin else ", ".join(projects)
    print("Token created successfully.")
    print(f"Name:   {args.token_name}")
    print(f"Token:  {token_value}")
    print(f"Access: {access}")
    print()
    print("Store this token securely. It cannot be retrieved later.")


def _handle_remove_token(args):
    """Remove a token by name from tokens.json."""
    data = load_json_file(TOKENS_FILE, {})
    tokens = data.get("tokens", {})

    token_key = None
    for key, meta in tokens.items():
        if meta.get("name") == args.remove_token:
            token_key = key
            break

    if token_key is None:
        print(f"Error: Token with name '{args.remove_token}' not found", file=sys.stderr)
        sys.exit(1)

    del tokens[token_key]
    data["tokens"] = tokens
    save_json_file(TOKENS_FILE, data)
    print(f"Token '{args.remove_token}' removed.")


def _handle_list_tokens():
    """List all tokens from tokens.json."""
    data = load_json_file(TOKENS_FILE, {})
    tokens = data.get("tokens", {})

    if not tokens:
        print("No tokens configured. Server runs without authentication.")
        return

    print(f"{'Name':<20} {'Token prefix':<14} {'Admin':<7} {'Projects':<30} {'Created'}")
    for token_value, meta in tokens.items():
        name = meta.get("name", "?")
        prefix = token_value[:8]
        admin = "yes" if meta.get("admin") else "no"
        projects = "(all)" if meta.get("admin") else ", ".join(meta.get("projects", []))
        created = meta.get("created", "?")
        print(f"{name:<20} {prefix:<14} {admin:<7} {projects:<30} {created}")


def _watchdog_loop(port, interval, threshold, timeout):
    """Daemon thread: poll /healthz, os._exit(1) on repeated failure.

    Runs OUTSIDE the worker pool so a fully-saturated pool still trips
    the watchdog (the GET /healthz attempt will time out or get a 503,
    counting as a failure). On `threshold` consecutive failures the
    process exits with code 1 — the container orchestrator (Docker
    restart_policy, Railway, Kubernetes) restarts us.

    Uses os._exit, not sys.exit: if workers are stuck inside I/O or a
    finalizer deadlock, normal interpreter teardown would hang too.
    """
    failures = 0
    while True:
        time.sleep(interval)
        try:
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
            try:
                conn.request('GET', '/healthz')
                resp = conn.getresponse()
                resp.read()
                ok = (resp.status == 200)
            finally:
                conn.close()
            if ok:
                failures = 0
                continue
            failures += 1
            print(f"[watchdog] /healthz returned {resp.status} ({failures}/{threshold})",
                  file=sys.stderr)
        except Exception as e:
            failures += 1
            print(f"[watchdog] /healthz error: {e} ({failures}/{threshold})",
                  file=sys.stderr)

        if failures >= threshold:
            print("[watchdog] failure threshold reached, exiting via os._exit(1)",
                  file=sys.stderr)
            sys.stderr.flush()
            sys.stdout.flush()
            os._exit(1)


def main():
    """Main server entry point."""
    parser = argparse.ArgumentParser(
        description='Build Number Counter Server',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # PaaS platforms (Railway, Heroku, Fly.io) inject the desired listen
    # port via $PORT. Honor it as the default when --port is not given.
    try:
        default_port = int(os.environ.get('PORT', '8080'))
    except ValueError:
        default_port = 8080
    parser.add_argument(
        '--port',
        type=int,
        default=default_port,
        help=f'Port to listen on (default: {default_port}; honors $PORT env var)'
    )
    parser.add_argument(
        '--host',
        default='0.0.0.0',
        help='Host to bind to (default: 0.0.0.0)'
    )
    parser.add_argument(
        '--data-dir',
        default=None,
        help='Directory for server data (default: server-data/ next to src/)'
    )
    parser.add_argument(
        '--accept-unknown',
        action='store_true',
        help='Automatically approve and add unknown project keys to build_numbers.json'
    )
    parser.add_argument(
        '--max-body-size',
        type=int,
        default=1024,
        help='Maximum allowed request body size in bytes (default: 1024)'
    )
    parser.add_argument(
        '--max-projects',
        type=int,
        default=100,
        help='Maximum number of projects allowed (default: 100, 0 = unlimited). Only enforced when --accept-unknown is enabled.'
    )

    parser.add_argument(
        '--max-threads',
        type=int,
        default=DEFAULT_MAX_WORKERS,
        metavar='N',
        help=f'Max concurrent worker threads (default: {DEFAULT_MAX_WORKERS}). '
             'Excess connections receive 503 Service Unavailable immediately.'
    )

    parser.add_argument(
        '--rate-limit',
        type=int,
        default=10,
        metavar='N',
        help='Max requests per minute per IP (default: 10). Set to 0 to disable.'
    )
    parser.add_argument(
        '--ban-duration',
        type=int,
        default=600,
        metavar='SECONDS',
        help='Temporary ban duration in seconds (default: 600). Ignored with --ban-permanent.'
    )
    parser.add_argument(
        '--ban-permanent',
        action='store_true',
        help='Use permanent bans (persisted to banned_ips.json) instead of temporary.'
    )

    parser.add_argument(
        '--watchdog',
        action='store_true',
        help='Enable in-process watchdog: poll GET /healthz; on repeated failure '
             'exit with code 1 so the container orchestrator restarts the process.'
    )
    parser.add_argument(
        '--watchdog-interval',
        type=int,
        default=10,
        metavar='SECS',
        help='Seconds between watchdog probes (default: 10).'
    )
    parser.add_argument(
        '--watchdog-failures',
        type=int,
        default=3,
        metavar='N',
        help='Consecutive failures before os._exit(1) (default: 3).'
    )
    parser.add_argument(
        '--watchdog-timeout',
        type=int,
        default=5,
        metavar='SECS',
        help='Per-probe HTTP client timeout in seconds (default: 5).'
    )

    counter_group = parser.add_argument_group('counter management')
    counter_group.add_argument('--set-counter', action='store_true',
                               help='Set a project counter to a specific value and exit (no server started)')
    counter_group.add_argument('--project-key', default=None,
                               help='Project key (used with --set-counter)')
    counter_group.add_argument('--version', type=int, default=None, dest='set_version', metavar='N',
                               help='Value to set the counter to (used with --set-counter)')

    token_group = parser.add_argument_group('token management')
    token_group.add_argument('--add-token', action='store_true',
                             help='Create a new API token and exit')
    token_group.add_argument('--remove-token', metavar='NAME',
                             help='Remove a token by name and exit')
    token_group.add_argument('--list-tokens', action='store_true',
                             help='List all tokens and exit')
    token_group.add_argument('--token-name', metavar='NAME',
                             help='Name for the new token (used with --add-token)')
    token_group.add_argument('--token-projects', metavar='KEYS',
                             help='Comma-separated project keys/patterns (used with --add-token)')
    token_group.add_argument('--token-admin', action='store_true',
                             help='Grant admin access to all projects (used with --add-token)')

    args = parser.parse_args()

    if args.max_projects < 0:
        parser.error("--max-projects must be >= 0")
    if args.max_body_size < 1:
        parser.error("--max-body-size must be >= 1")
    if args.max_threads < 1:
        parser.error("--max-threads must be >= 1")
    if args.rate_limit < 0:
        parser.error("--rate-limit must be >= 0")
    if args.ban_duration < 1:
        parser.error("--ban-duration must be >= 1")
    if args.watchdog_interval < 1:
        parser.error("--watchdog-interval must be >= 1")
    if args.watchdog_failures < 1:
        parser.error("--watchdog-failures must be >= 1")
    if args.watchdog_timeout < 1:
        parser.error("--watchdog-timeout must be >= 1")

    global accept_unknown, max_body_size, max_projects
    global rate_limit, ban_duration, ban_permanent
    accept_unknown = args.accept_unknown
    max_body_size = args.max_body_size
    max_projects = args.max_projects
    rate_limit = args.rate_limit
    ban_duration = args.ban_duration
    ban_permanent = args.ban_permanent

    # Initialize data directory
    if args.data_dir:
        data_dir = args.data_dir
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(script_dir)
        data_dir = os.path.join(project_root, "server-data")

    init_data_dir(data_dir)

    # Handle token management commands (exit without starting server)
    if args.add_token:
        _handle_add_token(args)
        return
    if args.remove_token:
        _handle_remove_token(args)
        return
    if args.list_tokens:
        _handle_list_tokens()
        return

    # Handle --set-counter (offline operation, no HTTP server)
    if args.set_counter:
        if not args.project_key:
            print("Error: --set-counter requires --project-key", file=sys.stderr)
            sys.exit(1)
        if args.set_version is None:
            print("Error: --set-counter requires --version", file=sys.stderr)
            sys.exit(1)
        if args.set_version < 0:
            print("Error: --version must be >= 0", file=sys.stderr)
            sys.exit(1)
        try:
            validate_project_key(args.project_key)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        set_build_number(args.project_key, args.set_version, force_unapproved=True)
        print(f"Set {args.project_key} = {args.set_version}")
        return

    # Initialize build numbers file if it doesn't exist
    if not os.path.exists(BUILD_NUMBERS_FILE):
        save_json_file(BUILD_NUMBERS_FILE, {})
        print(f"Created {BUILD_NUMBERS_FILE}")
        print("Add project keys to this file to approve them, or use --accept-unknown flag")

    # Start server
    server = PooledHTTPServer(
        (args.host, args.port),
        BuildNumberHandler,
        max_workers=args.max_threads,
    )

    print(f"Build Number Counter Server starting...")
    print(f"Listening on {args.host}:{args.port}")
    print(f"Data directory: {DATA_DIR}")
    print(f"Auto-approve unknown projects: {accept_unknown}")
    print(f"Max body size: {max_body_size} bytes")
    print(f"Max projects: {'unlimited' if max_projects == 0 else max_projects}"
          f"{' (only enforced with --accept-unknown)' if max_projects > 0 else ''}")
    print(f"Worker threads: {args.max_threads} (queue: {args.max_threads})")
    if args.watchdog:
        print(f"Watchdog: enabled (interval={args.watchdog_interval}s, "
              f"failures={args.watchdog_failures}, timeout={args.watchdog_timeout}s)")
    else:
        print("Watchdog: disabled")
    if rate_limit > 0:
        ban_info = "permanent" if ban_permanent else f"temporary ({ban_duration}s)"
        print(f"Rate limit: {rate_limit} req/min per IP, ban: {ban_info}")
    else:
        print("Rate limit: disabled")
    tokens = load_tokens()
    if tokens:
        print(f"Authentication: enabled ({len(tokens)} token(s))")
    else:
        print("Authentication: disabled (no tokens configured)")
    print(f"Press Ctrl+C to stop\n")

    if rate_limit > 0:
        _start_cleanup_timer()

    if args.watchdog:
        watchdog_thread = threading.Thread(
            target=_watchdog_loop,
            args=(args.port, args.watchdog_interval,
                  args.watchdog_failures, args.watchdog_timeout),
            name='watchdog',
            daemon=True,
        )
        watchdog_thread.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.shutdown()
        print("Server stopped.")


if __name__ == '__main__':
    main()
