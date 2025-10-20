#!/usr/bin/env python3
"""
Build Number Counter Server

A simple HTTP server that manages build numbers for multiple projects.
Provides atomic increment operations with persistent storage.
"""

import json
import argparse
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# Global state (initialized in main() or by test fixtures)
DATA_DIR = None
BUILD_NUMBERS_FILE = None
file_lock = threading.Lock()
accept_unknown = False


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


def increment_build_number(project_key, local_version=None):
    """
    Increment build number for a project.

    Args:
        project_key: Unique identifier for the project
        local_version: Optional local version to sync (use if greater than server's)

    Returns:
        New build number or None on error
    """
    with file_lock:
        build_numbers = load_json_file(BUILD_NUMBERS_FILE, {})

        current = build_numbers.get(project_key, 0)

        # If local version is provided and greater, use it
        if local_version is not None and local_version > current:
            current = local_version
            print(f"Updated {project_key} from local version: {local_version}")

        # Increment
        new_number = current + 1
        build_numbers[project_key] = new_number

        # Save atomically
        save_json_file(BUILD_NUMBERS_FILE, build_numbers)

        print(f"Incremented {project_key}: {current} -> {new_number}")
        return new_number


class BuildNumberHandler(BaseHTTPRequestHandler):
    """HTTP request handler for build number operations."""

    def log_message(self, format, *args):
        """Override to customize logging."""
        sys.stdout.write(f"[{self.log_date_time_string()}] {format % args}\n")

    def send_json_response(self, status_code, data):
        """Send JSON response."""
        self.send_response(status_code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def do_POST(self):
        """Handle POST requests."""
        parsed_path = urlparse(self.path)

        if parsed_path.path == '/increment':
            try:
                # Read request body
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length).decode('utf-8')
                data = json.loads(body) if body else {}

                project_key = data.get('project_key')
                local_version = data.get('local_version')

                if not project_key:
                    self.send_json_response(400, {
                        'error': 'Missing project_key parameter'
                    })
                    return

                # Check if project is approved (exists in build_numbers.json)
                if not is_project_approved(project_key):
                    if not accept_unknown:
                        self.send_json_response(403, {
                            'error': f'Project key "{project_key}" is not approved. Add it to build_numbers.json or restart server with --accept-unknown',
                            'project_key': project_key
                        })
                        return
                    # If accept_unknown is enabled, project will be added automatically in increment_build_number

                # Increment and return
                build_number = increment_build_number(project_key, local_version)

                if build_number is not None:
                    self.send_json_response(200, {
                        'build_number': build_number,
                        'project_key': project_key
                    })
                else:
                    self.send_json_response(500, {
                        'error': 'Failed to increment build number'
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
        else:
            self.send_json_response(404, {
                'error': 'Not found',
                'available_endpoints': ['/increment (POST)']
            })

    def do_GET(self):
        """Handle GET requests (status/info only)."""
        parsed_path = urlparse(self.path)

        if parsed_path.path == '/':
            self.send_json_response(200, {
                'service': 'Build Number Counter Server',
                'endpoints': {
                    '/increment': 'POST with JSON body: {"project_key": "key", "local_version": N (optional)}'
                }
            })
        else:
            self.send_json_response(404, {
                'error': 'Not found'
            })


def init_data_dir(data_dir):
    """Initialize data directory and build numbers file."""
    global DATA_DIR, BUILD_NUMBERS_FILE
    DATA_DIR = data_dir
    BUILD_NUMBERS_FILE = os.path.join(DATA_DIR, "build_numbers.json")
    os.makedirs(DATA_DIR, exist_ok=True)


def main():
    """Main server entry point."""
    parser = argparse.ArgumentParser(
        description='Build Number Counter Server',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--port',
        type=int,
        default=8080,
        help='Port to listen on (default: 8080)'
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

    args = parser.parse_args()

    global accept_unknown
    accept_unknown = args.accept_unknown

    # Initialize data directory
    if args.data_dir:
        data_dir = args.data_dir
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(script_dir)
        data_dir = os.path.join(project_root, "server-data")

    init_data_dir(data_dir)

    # Initialize build numbers file if it doesn't exist
    if not os.path.exists(BUILD_NUMBERS_FILE):
        save_json_file(BUILD_NUMBERS_FILE, {})
        print(f"Created {BUILD_NUMBERS_FILE}")
        print("Add project keys to this file to approve them, or use --accept-unknown flag")

    # Start server
    server = HTTPServer((args.host, args.port), BuildNumberHandler)

    print(f"Build Number Counter Server starting...")
    print(f"Listening on {args.host}:{args.port}")
    print(f"Data directory: {DATA_DIR}")
    print(f"Auto-approve unknown projects: {accept_unknown}")
    print(f"Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.shutdown()
        print("Server stopped.")


if __name__ == '__main__':
    main()
