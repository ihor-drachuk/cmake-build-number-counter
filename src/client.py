#!/usr/bin/env python3
"""
Build Number Counter Client

Client script for fetching and incrementing build numbers from a central server,
with local fallback support when the server is unavailable.
"""

import json
import argparse
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

# Default local file for storing fallback counter
DEFAULT_LOCAL_FILE = "build_number.txt"


def log_message(message, file=sys.stderr):
    """Print log message to stderr."""
    print(message, file=file)


def load_local_counter(local_file):
    """Load local build counter from file."""
    if not os.path.exists(local_file):
        return 0
    try:
        with open(local_file, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            return int(content) if content else 0
    except (ValueError, IOError) as e:
        log_message(f"Warning: Error reading local counter: {e}")
        return 0


def save_local_counter(local_file, value):
    """Save local build counter to file atomically."""
    # Ensure directory exists
    os.makedirs(os.path.dirname(local_file) if os.path.dirname(local_file) else '.', exist_ok=True)

    # Write atomically
    temp_file = local_file + ".tmp"
    with open(temp_file, 'w', encoding='utf-8') as f:
        f.write(str(value))

    # Atomic replace
    os.replace(temp_file, local_file)


def load_local_sync_state(local_file):
    """Load synchronization state (tracks if local counter needs to sync to server)."""
    sync_file = local_file + ".sync"
    if not os.path.exists(sync_file):
        return None
    try:
        with open(sync_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('local_version')
    except (json.JSONDecodeError, IOError):
        return None


def save_local_sync_state(local_file, local_version):
    """Save synchronization state."""
    sync_file = local_file + ".sync"
    with open(sync_file, 'w', encoding='utf-8') as f:
        json.dump({'local_version': local_version}, f)


def clear_local_sync_state(local_file):
    """Clear synchronization state after successful sync."""
    sync_file = local_file + ".sync"
    if os.path.exists(sync_file):
        os.remove(sync_file)


def increment_on_server(server_url, project_key, local_version=None):
    """
    Request build number increment from server.

    Args:
        server_url: Base URL of the server
        project_key: Project identifier
        local_version: Optional local version to sync

    Returns:
        Build number on success, None on failure
    """
    url = f"{server_url.rstrip('/')}/increment"

    request_data = {'project_key': project_key}
    if local_version is not None:
        request_data['local_version'] = local_version

    data = json.dumps(request_data).encode('utf-8')

    try:
        req = urllib.request.Request(
            url,
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )

        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result.get('build_number')

    except urllib.error.HTTPError as e:
        try:
            error_data = json.loads(e.read().decode('utf-8'))
            error_msg = error_data.get('error', str(e))
        except Exception:
            error_msg = str(e)
        log_message(f"Server error: {error_msg}")
        return None
    except urllib.error.URLError as e:
        log_message(f"Connection error: {e.reason}")
        return None
    except Exception as e:
        log_message(f"Request failed: {e}")
        return None


def increment_locally(local_file, project_key):
    """
    Increment build number locally (fallback mode).

    Args:
        local_file: Path to local counter file
        project_key: Project identifier (for logging)

    Returns:
        New build number
    """
    current = load_local_counter(local_file)
    new_number = current + 1
    save_local_counter(local_file, new_number)
    save_local_sync_state(local_file, new_number)

    log_message(f"WARNING: Using LOCAL build number for '{project_key}': {new_number}")
    log_message(f"Local counter will sync to server on next successful connection")

    return new_number


def get_build_number(project_key, server_url=None, local_file=DEFAULT_LOCAL_FILE):
    """
    Get incremented build number, trying server first, then local fallback.

    Args:
        project_key: Project identifier
        server_url: Server URL (optional)
        local_file: Path to local counter file

    Returns:
        Tuple of (build_number, was_local)
    """
    # Check if we have a pending local version to sync
    pending_sync = load_local_sync_state(local_file)

    # Try server first if URL is provided
    if server_url:
        build_number = increment_on_server(server_url, project_key, pending_sync)

        if build_number is not None:
            # Server succeeded
            if pending_sync is not None:
                log_message(f"Successfully synced local version {pending_sync} to server")
                clear_local_sync_state(local_file)

            # Update local counter to match server
            save_local_counter(local_file, build_number)
            log_message(f"Build number from server: {build_number}")
            return build_number, False

    # Server not available or no URL provided - use local fallback
    build_number = increment_locally(local_file, project_key)
    return build_number, True


def format_output(build_number, output_format, project_key):
    """
    Format the build number for output.

    Args:
        build_number: The build number
        output_format: Output format (plain, cmake, json)
        project_key: Project identifier

    Returns:
        Formatted string
    """
    if output_format == 'plain':
        return str(build_number)
    elif output_format == 'cmake':
        return f'set(BUILD_NUMBER "{build_number}")'
    elif output_format == 'json':
        return json.dumps({
            'build_number': build_number,
            'project_key': project_key
        })
    else:
        return str(build_number)


def main():
    """Main client entry point."""
    parser = argparse.ArgumentParser(
        description='Build Number Counter Client',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --project-key myproject --server-url http://buildserver:8080
  %(prog)s --project-key myproject --local-file ./build_number.txt
  %(prog)s --project-key myproject  # Uses BUILD_SERVER_URL env var
"""
    )

    parser.add_argument(
        '--project-key',
        required=True,
        help='Unique identifier for the project'
    )
    parser.add_argument(
        '--server-url',
        help='Build number server URL (overrides BUILD_SERVER_URL env var)'
    )
    parser.add_argument(
        '--local-file',
        default=DEFAULT_LOCAL_FILE,
        help=f'Local counter file for fallback (default: {DEFAULT_LOCAL_FILE})'
    )
    parser.add_argument(
        '--output-format',
        choices=['plain', 'cmake', 'json'],
        default='plain',
        help='Output format (default: plain)'
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Suppress log messages (only output the build number)'
    )

    args = parser.parse_args()

    # Get server URL from argument or environment
    server_url = args.server_url or os.environ.get('BUILD_SERVER_URL')

    # Suppress logs if quiet mode
    if args.quiet:
        global log_message
        log_message = lambda *a, **k: None

    # Get build number
    build_number, was_local = get_build_number(
        args.project_key,
        server_url,
        args.local_file
    )

    # Output result
    output = format_output(build_number, args.output_format, args.project_key)
    print(output)

    return 0


if __name__ == '__main__':
    sys.exit(main())

