#!/usr/bin/env python3
"""
Build Number Counter Server

A simple HTTP server that manages build numbers for multiple projects.
Provides atomic increment operations with persistent storage.
"""

import enum
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
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse
from validation import validate_project_key


DEFAULT_MAX_WORKERS = 64

# Network errors we silence in handle_error: client disconnects and idle
# timeouts are normal traffic, not server bugs. Real exceptions still
# surface through the standard handle_error path.
_BENIGN_NETWORK_ERRORS = (ConnectionError, socket.timeout, TimeoutError)


class PooledHTTPServer(HTTPServer):
    """HTTP server with a fixed worker thread pool and bounded queue.

    Hard cap on concurrent connections: max_workers in flight + max_workers
    queued. Excess clients have their socket closed immediately on the
    accept thread (no body, no I/O) — see _refuse_overloaded.

    Workers are daemon threads; we never join them on shutdown so a
    stuck handler cannot block server_close. See ADR-003 for the design
    rationale (why not ThreadPoolExecutor, why bounded queue size =
    max_workers, etc).
    """

    def __init__(self, server_address, RequestHandlerClass,
                 bind_and_activate=True, max_workers=None):
        super().__init__(server_address, RequestHandlerClass, bind_and_activate)
        self._max_workers = max_workers or DEFAULT_MAX_WORKERS
        self._work_queue = queue.Queue(maxsize=self._max_workers)
        self._shutdown_event = threading.Event()
        self._workers = []
        # Per-instance uptime origin. /healthz reads it via self.server.
        self._start_time = time.monotonic()
        for i in range(self._max_workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f'cbnc-worker-{i}',
                daemon=True,
            )
            t.start()
            self._workers.append(t)

    def process_request(self, request, client_address):
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

    def _refuse_overloaded(self, request):
        # Runs on the accept thread. Any synchronous I/O here stalls
        # accept() and defeats the bounded-queue design — even a 100ms
        # send timeout, multiplied across sustained overload, steals
        # real capacity. We just close. Clients see RST/FIN; ADR-001
        # documents this as the expected early-rejection behavior.
        try:
            self.shutdown_request(request)
        except OSError:
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
rate_lock = threading.Lock()  # protects in-memory rate state only

# Separate lock for banned_ips.json disk I/O. Held while reading or
# writing the file; never held while holding rate_lock (and vice versa)
# so a slow disk cannot serialize all rate-limit decisions.
_bans_file_lock = threading.Lock()

# Tokens cache (mtime-invalidated). Keeps load_tokens() off the hot disk
# I/O path on every authenticated request. Sentinel mtime -1.0 means
# "cache empty / file absent on last check".
_tokens_cache = {}
_tokens_cache_mtime = -1.0
_tokens_cache_lock = threading.Lock()


class _BodyTooLarge(Exception):
    def __init__(self, size):
        self.size = size


def load_json_file(filename: str, default: Any) -> Any:
    """Load JSON file with error handling."""
    if not os.path.exists(filename):
        return default
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Error reading {filename}: {e}", file=sys.stderr)
        return default


def save_json_file(filename: str, data: Any) -> None:
    # fsync before os.replace: atomic replace covers the dirent but not
    # the data blocks. Without fsync, a crash mid-write (kernel panic,
    # watchdog os._exit + container SIGKILL) can leave the dirent
    # pointing at zero-length data.
    temp_file = filename + ".tmp"
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_file, filename)


def load_tokens() -> Dict[str, Any]:
    # Returns a shallow COPY (not the live _tokens_cache reference) so
    # caller mutation cannot poison the cache for other threads, and a
    # concurrent refresh cannot make a reader's iteration crash.
    global _tokens_cache, _tokens_cache_mtime

    if TOKENS_FILE is None or not os.path.exists(TOKENS_FILE):
        with _tokens_cache_lock:
            if _tokens_cache_mtime != -1.0:
                _tokens_cache = {}
                _tokens_cache_mtime = -1.0
            return dict(_tokens_cache)

    try:
        mtime = os.path.getmtime(TOKENS_FILE)
    except OSError:
        with _tokens_cache_lock:
            return dict(_tokens_cache)

    with _tokens_cache_lock:
        if mtime != _tokens_cache_mtime:
            data = load_json_file(TOKENS_FILE, {})
            _tokens_cache = data.get("tokens", {})
            _tokens_cache_mtime = mtime
        return dict(_tokens_cache)


def authenticate_request(handler: BaseHTTPRequestHandler,
                         project_key: str) -> Tuple[bool, Optional[str]]:
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


def _refresh_permanent_bans_from_disk() -> set:
    # Disk I/O under _bans_file_lock (NOT rate_lock) so a slow disk
    # cannot stall every concurrent request's rate-limit decision.
    global permanent_bans, permanent_bans_mtime

    if DATA_DIR is None:
        return permanent_bans
    ban_file = os.path.join(DATA_DIR, "banned_ips.json")

    with _bans_file_lock:
        try:
            mtime = os.path.getmtime(ban_file)
        except OSError:
            return permanent_bans
        if mtime == permanent_bans_mtime:
            return permanent_bans
        data = load_json_file(ban_file, {"banned": {}})
        new_set = set(data.get("banned", {}).keys())
        permanent_bans = new_set
        permanent_bans_mtime = mtime
        return new_set


def _persist_permanent_ban(ip: str) -> None:
    """Append `ip` to banned_ips.json. Called WITHOUT rate_lock held."""
    global permanent_bans_mtime

    if DATA_DIR is None:
        return
    ban_file = os.path.join(DATA_DIR, "banned_ips.json")

    with _bans_file_lock:
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


def check_rate_limit(handler: BaseHTTPRequestHandler) -> bool:
    # Returns True if request is allowed, False if rejected (429 sent).
    # Disk I/O for banned_ips.json never happens under rate_lock: a fresh
    # read is done before the lock if needed, and persisting a new ban
    # happens after the lock is released.
    if rate_limit <= 0:
        return True

    ip = handler.client_address[0]
    now = time.monotonic()

    # If permanent bans are in use, refresh the cache OUTSIDE rate_lock.
    # The mtime check inside _refresh_permanent_bans_from_disk is cheap
    # when nothing changed (single os.stat).
    if ban_permanent:
        _refresh_permanent_bans_from_disk()

    persist_after = False  # set True if we decide to persist a new ban

    with rate_lock:
        if ban_permanent and ip in permanent_bans:
            _send_429_permanent(handler, ip)
            return False

        if not ban_permanent and ip in temp_bans:
            expiry = temp_bans[ip]
            if now < expiry:
                _send_429_temporary(handler, ip, int(expiry - now))
                return False
            else:
                del temp_bans[ip]

        timestamps = rate_tracker.get(ip, [])
        cutoff = now - 60.0
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)

        if len(timestamps) >= rate_limit:
            # Mutate in-memory state under the lock; persist after release.
            if ban_permanent:
                permanent_bans.add(ip)
                persist_after = True
                print(f"Permanently banned IP: {ip}")
            else:
                temp_bans[ip] = now + ban_duration
                print(f"Temporarily banned IP: {ip} for {ban_duration}s")
            rate_tracker.pop(ip, None)
            response_pending = True
        else:
            timestamps.append(now)
            rate_tracker[ip] = timestamps
            response_pending = False

    if persist_after:
        _persist_permanent_ban(ip)

    if response_pending:
        if ban_permanent:
            _send_429_permanent(handler, ip)
        else:
            _send_429_temporary(handler, ip, ban_duration)
        return False

    return True


def _send_429_permanent(handler: BaseHTTPRequestHandler, ip: str) -> None:
    handler.send_json_response(429, {
        'error': 'Permanently banned due to rate limit violation',
        'ban_type': 'permanent',
        'ip': ip,
    })


def _send_429_temporary(handler: BaseHTTPRequestHandler, ip: str,
                        retry_after_seconds: int) -> None:
    handler.send_json_response(429, {
        'error': 'Temporarily banned due to rate limit violation',
        'ban_type': 'temporary',
        'retry_after_seconds': retry_after_seconds,
        'ip': ip,
    })


def cleanup_rate_data() -> None:
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


def _start_cleanup_timer() -> None:
    """Start periodic cleanup of rate limiting data."""
    cleanup_rate_data()
    timer = threading.Timer(60.0, _start_cleanup_timer)
    timer.daemon = True
    timer.start()


def validate_local_version(value: Any) -> Tuple[bool, Optional[str]]:
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


def validate_version(value: Any) -> Tuple[bool, Optional[str]]:
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


class _IncrementStatus(enum.Enum):
    OK = "ok"
    UNAPPROVED = "unapproved"
    LIMIT_REACHED = "limit_reached"


class _SetStatus(enum.Enum):
    OK = "ok"
    UNAPPROVED = "unapproved"


@dataclass
class _IncrementResult:
    status: _IncrementStatus
    build_number: Optional[int] = None


@dataclass
class _SetResult:
    status: _SetStatus
    build_number: Optional[int] = None


def increment_build_number(project_key: str,
                           local_version: Optional[int] = None) -> _IncrementResult:
    # Approval/limit checks live INSIDE file_lock together with the
    # increment to avoid TOCTOU under the worker pool.
    with file_lock:
        build_numbers = load_json_file(BUILD_NUMBERS_FILE, {})
        is_new = project_key not in build_numbers

        if is_new and not accept_unknown:
            return _IncrementResult(status=_IncrementStatus.UNAPPROVED)

        if is_new and max_projects > 0 and len(build_numbers) >= max_projects:
            return _IncrementResult(status=_IncrementStatus.LIMIT_REACHED)

        current = build_numbers.get(project_key, 0)
        if local_version is not None and local_version > current:
            current = local_version
            print(f"Updated {project_key} from local version: {local_version}")

        new_number = current + 1
        build_numbers[project_key] = new_number
        save_json_file(BUILD_NUMBERS_FILE, build_numbers)

        print(f"Incremented {project_key}: {current} -> {new_number}")
        return _IncrementResult(status=_IncrementStatus.OK, build_number=new_number)


def set_build_number(project_key: str, version: int,
                     force_unapproved: bool = False) -> _SetResult:
    # force_unapproved=True is used by the --set-counter CLI: a local
    # admin operation that must succeed regardless of accept_unknown.
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise ValueError(f"version must be a non-negative integer, got {version!r}")

    with file_lock:
        build_numbers = load_json_file(BUILD_NUMBERS_FILE, {})
        is_new = project_key not in build_numbers

        if is_new and not accept_unknown and not force_unapproved:
            return _SetResult(status=_SetStatus.UNAPPROVED)

        build_numbers[project_key] = version
        save_json_file(BUILD_NUMBERS_FILE, build_numbers)
        print(f"Set {project_key} to {version}")
        return _SetResult(status=_SetStatus.OK, build_number=version)


class BuildNumberHandler(BaseHTTPRequestHandler):
    """HTTP request handler for build number operations."""

    # Per-recv socket timeout (StreamRequestHandler.setup applies it to
    # self.connection). Caps the wait on any single read/write. Combined
    # with the wall-clock deadline below, this defends against Slowloris:
    # per-recv alone is bypassable by drip-feeding bytes just under the
    # timeout; the wall-clock deadline puts a hard ceiling regardless.
    timeout = 1

    # Total wall-clock seconds a single request may occupy a worker.
    # Bodies are bounded by --max-body-size (default 1 KB), so 5 s is
    # generous for any legitimate client. Drip-feed Slowloris hits
    # this limit and the connection is closed forcibly.
    max_request_seconds = 5

    def setup(self):
        super().setup()
        # Hard wall-clock deadline. The timer fires on a daemon thread
        # and shuts the socket down for both directions, which forces
        # any blocked rfile.read/wfile.write to raise OSError so the
        # worker can move on. We do NOT close the socket here — the
        # worker still owns its lifecycle (shutdown_request will close
        # it normally).
        sock = self.connection
        def _hard_kill():
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._deadline_timer = threading.Timer(self.max_request_seconds, _hard_kill)
        self._deadline_timer.daemon = True
        self._deadline_timer.start()

    def finish(self):
        # Cancel the deadline timer if the request finished before it fired.
        timer = getattr(self, '_deadline_timer', None)
        if timer is not None:
            timer.cancel()
        super().finish()

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
        except (socket.timeout, TimeoutError, OSError):
            # Per-recv timeout, wall-clock deadline (which shuts the
            # socket down → OSError on next read), or generic socket
            # error mid-body. All three count as Slowloris/abandoned;
            # respond 408 best-effort and abort.
            try:
                self.send_json_response(408, {
                    'error': 'Request timeout while reading body'
                })
            except (OSError, socket.timeout, TimeoutError, BrokenPipeError):
                pass
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

                if result.status is _IncrementStatus.UNAPPROVED:
                    self.send_json_response(403, {
                        'error': f'Project key "{project_key}" is not approved. Add it to build_numbers.json or restart server with --accept-unknown',
                        'project_key': project_key
                    })
                elif result.status is _IncrementStatus.LIMIT_REACHED:
                    self.send_json_response(507, {
                        'error': 'Maximum project limit reached',
                        'detail': f'Server is configured to allow at most {max_projects} projects. '
                                  'Contact the server administrator to increase the limit '
                                  'or remove unused projects.',
                        'max_projects': max_projects,
                        'project_key': project_key,
                    })
                elif result.status is _IncrementStatus.OK:
                    self.send_json_response(200, {
                        'build_number': result.build_number,
                        'project_key': project_key
                    })
                else:
                    # Defensive: enum exhaustiveness — should never reach here.
                    self.send_json_response(500, {
                        'error': f'Unexpected increment status: {result.status!r}'
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

                if result.status is _SetStatus.UNAPPROVED:
                    self.send_json_response(403, {
                        'error': f'Project key "{project_key}" is not approved',
                        'project_key': project_key
                    })
                elif result.status is _SetStatus.OK:
                    self.send_json_response(200, {
                        'build_number': result.build_number,
                        'project_key': project_key
                    })
                else:
                    # Defensive: enum exhaustiveness — should never reach here.
                    self.send_json_response(500, {
                        'error': f'Unexpected set status: {result.status!r}'
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

        if parsed_path.path == '/healthz':
            # Loopback callers (in-process watchdog, Docker HEALTHCHECK in
            # bridge mode) bypass rate-limit AND see the full payload
            # including queue_depth. External callers go through normal
            # rate-limit and see a minimal payload — queue_depth is a
            # real-time DoS oracle, do not expose it publicly.
            client_ip = self.client_address[0]
            is_loopback = client_ip in ('127.0.0.1', '::1', '::ffff:127.0.0.1')
            if not is_loopback and not check_rate_limit(self):
                return
            try:
                payload = {
                    'status': 'ok',
                    'uptime_seconds': int(time.monotonic() - self.server._start_time),
                }
                if is_loopback:
                    payload['workers'] = self.server._max_workers
                    payload['queue_depth'] = self.server._work_queue.qsize()
                self.send_json_response(200, payload)
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


def init_data_dir(data_dir: str) -> None:
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


def _watchdog_loop(port: int, interval: float, threshold: int,
                   timeout: float) -> None:
    # os._exit, not sys.exit: in the failure mode we are guarding against
    # (workers stuck in I/O / deadlocked finalizer), normal teardown
    # could itself hang. See ADR-003.
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
        except (OSError, http.client.HTTPException, socket.timeout, TimeoutError) as e:
            # Narrow except: anything else (NameError, AttributeError from a
            # programming bug) propagates a real traceback instead of being
            # masked as a failure-tick that slowly walks us toward os._exit.
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

    # Disk-fill DoS warning: with no project cap, accept-unknown ON, and
    # no auth, anyone can spam project keys until the JSON file fills the
    # disk. Per-IP rate limit slows it down but does not stop a botnet.
    if max_projects == 0 and accept_unknown and not tokens:
        print("WARNING: --max-projects 0 + --accept-unknown + no tokens is "
              "vulnerable to disk-fill DoS via crafted project keys. "
              "Set --max-projects, configure tokens, or restrict network access.",
              file=sys.stderr)

    print(f"Press Ctrl+C to stop\n")

    if rate_limit > 0:
        _start_cleanup_timer()

    if args.watchdog:
        # Watchdog probes 127.0.0.1. If the operator bound to a single
        # non-loopback address, the watchdog will get connection-refused
        # forever and kill the server in interval×failures seconds.
        _LOOPBACK_HOSTS = {'0.0.0.0', '::', '127.0.0.1', '::1', 'localhost', ''}
        if args.host not in _LOOPBACK_HOSTS:
            print(f"WARNING: --watchdog probes 127.0.0.1 but --host is "
                  f"'{args.host}'. The server is not reachable on loopback, "
                  f"so the watchdog will trip in "
                  f"{args.watchdog_interval * args.watchdog_failures}s and "
                  f"kill the process. Bind to 0.0.0.0 or disable --watchdog.",
                  file=sys.stderr)

        # Use the actual bound port, not args.port. With --port 0 the
        # kernel chose a free port and args.port is still 0, so probing
        # 127.0.0.1:0 would fail forever and kill the server in 30s.
        bound_port = server.server_address[1]
        watchdog_thread = threading.Thread(
            target=_watchdog_loop,
            args=(bound_port, args.watchdog_interval,
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
