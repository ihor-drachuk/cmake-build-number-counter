"""Shared validation for build-number-counter."""

import re

PROJECT_KEY_PATTERN = re.compile(r'^[a-zA-Z0-9._-]+$')
PROJECT_KEY_MAX_LENGTH = 128


def validate_project_key(key):
    """
    Validate a project key.

    Raises:
        ValueError with a human-readable message if invalid.
    """
    if not isinstance(key, str) or len(key) == 0:
        raise ValueError(
            "Invalid project_key: must be 1-128 characters, "
            "allowed: [a-zA-Z0-9._-]"
        )
    if len(key) > PROJECT_KEY_MAX_LENGTH:
        raise ValueError(
            "Invalid project_key: must be 1-128 characters, "
            "allowed: [a-zA-Z0-9._-]"
        )
    if not PROJECT_KEY_PATTERN.match(key):
        raise ValueError(
            "Invalid project_key: must be 1-128 characters, "
            "allowed: [a-zA-Z0-9._-]"
        )
