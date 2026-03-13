import pytest

from validation import validate_project_key


class TestValidateProjectKey:
    @pytest.mark.parametrize("key", [
        "myproject",
        "my-project",
        "my_project",
        "my.project",
        "MyApp.2",
        "a",
        "a" * 128,
        "-leading-hyphen",
        ".leading-dot",
        "trailing-dot.",
        "UPPER",
        "lower",
        "MiXeD.CaSe-123_foo",
        "0123456789",
    ])
    def test_valid_keys(self, key):
        validate_project_key(key)  # should not raise

    @pytest.mark.parametrize("key,description", [
        ("", "empty string"),
        ("a" * 129, "exceeds max length"),
        ("my project", "contains space"),
        ("my/project", "forward slash"),
        ("my\\project", "backslash"),
        ("../../etc/passwd", "path traversal unix"),
        ("..\\..\\Windows", "path traversal windows"),
        ('my"project', "double quote"),
        ("my'project", "single quote"),
        ("${injection}", "cmake variable expansion"),
        ("proj\nfake", "newline"),
        ("proj\x00null", "null byte"),
        ("\t\t", "tabs only"),
        ("   ", "spaces only"),
        ("key@host", "at sign"),
        ("key:value", "colon"),
        ("key;drop", "semicolon"),
        ("a|b", "pipe"),
        ("a&b", "ampersand"),
    ])
    def test_invalid_keys(self, key, description):
        with pytest.raises(ValueError):
            validate_project_key(key)

    @pytest.mark.parametrize("key", [None, 123, 12.5, [], {}])
    def test_non_string_types(self, key):
        with pytest.raises(ValueError):
            validate_project_key(key)
