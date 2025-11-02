import pytest

from schemathesis.core.shell import (
    ShellType,
    _escape_with_ansi_c,
    _escape_with_hex,
    _parse_shell_name,
    detect_shell,
    escape_for_shell,
    has_non_printable,
)


@pytest.mark.parametrize(
    ("input_value", "expected"),
    [
        ("hello world", False),
        ("hello\tworld", True),
        ("hello\nworld", True),
        ("hello\x00world", True),
        ("hello\x1fworld", True),
        ("hello\x7fworld", True),
        (b"hello\x00world", True),
        (b"\xff\xfe", True),
        ("hello 世界", False),
    ],
    ids=[
        "printable-string",
        "with-tab",
        "with-newline",
        "with-null-byte",
        "with-control-char",
        "with-del",
        "bytes-input",
        "invalid-utf8-bytes",
        "unicode-printable",
    ],
)
def test_has_non_printable(input_value, expected):
    assert has_non_printable(input_value) == expected


@pytest.mark.parametrize(
    ("input_value", "expected"),
    [
        ("hello", "hello"),
        ("a\tb", "a\\tb"),
        ("a\nb", "a\\nb"),
        ("a\rb", "a\\rb"),
        ("a\x00b", "a\\x00b"),
        ("a\x1fb", "a\\x1fb"),
        ("a\x7fb", "a\\x7fb"),
        ("it's", "it\\'s"),
        ("a\\b", "a\\\\b"),
        ("$VAR", "\\$VAR"),
        ("`cmd`", "\\`cmd\\`"),
        ("0\x1f", "0\\x1f"),
    ],
    ids=[
        "printable-string",
        "with-tab",
        "with-newline",
        "with-carriage-return",
        "with-null-byte",
        "with-control-char",
        "with-del",
        "escapes-single-quote",
        "escapes-backslash",
        "escapes-dollar",
        "escapes-backtick",
        "complex-case",
    ],
)
def test_escape_with_ansi_c(input_value, expected):
    assert _escape_with_ansi_c(input_value) == expected


@pytest.mark.parametrize(
    ("input_value", "expected"),
    [
        ("hello", "hello"),
        ("a\tb", "a\\tb"),
        ("a\nb", "a\\nb"),
        ("a\x00b", "a\\x00b"),
        ("a\x1fb", "a\\x1fb"),
        ("it's", "it\\'s"),
        ("a\\b", "a\\\\b"),
    ],
    ids=[
        "printable-string",
        "with-tab",
        "with-newline",
        "with-null-byte",
        "with-control-char",
        "escapes-single-quote",
        "escapes-backslash",
    ],
)
def test_escape_with_hex(input_value, expected):
    assert _escape_with_hex(input_value) == expected


@pytest.mark.parametrize(
    ("input_value", "shell_type", "expected_escaped", "expected_warning", "expected_shell"),
    [
        ("hello", ShellType.BASH, "hello", False, ShellType.BASH),
        ("0\x1f", ShellType.BASH, "$'0\\x1f'", False, ShellType.BASH),
        ("0\x1f", ShellType.ZSH, "$'0\\x1f'", False, ShellType.ZSH),
        ("0\x1f", ShellType.FISH, "'0\\x1f'", False, ShellType.FISH),
        ("0\x1f", ShellType.UNKNOWN, "$'0\\x1f'", True, ShellType.BASH),
        # autodetect
        ("hello", None, "hello", False, None),
    ],
    ids=[
        "bash-printable",
        "bash-non-printable",
        "zsh-non-printable",
        "fish-non-printable",
        "unknown-shell",
        "autodetect",
    ],
)
def test_escape_for_shell(input_value, shell_type, expected_escaped, expected_warning, expected_shell):
    result = escape_for_shell(input_value, shell_type)
    assert result.escaped_value == expected_escaped
    assert result.needs_warning == expected_warning
    if expected_shell is not None:
        assert result.shell_used == expected_shell
    if expected_warning:
        assert result.original_bytes == b"0\x1f"


def test_detect_shell_caches_result():
    shell1 = detect_shell()
    shell2 = detect_shell()
    assert shell1 == shell2


@pytest.mark.parametrize(
    ("input_value", "expected_bash", "expected_fish"),
    [
        ("hello", "hello", "hello"),
        ("a\tb", "a\\tb", "a\\tb"),
        ("a\nb", "a\\nb", "a\\nb"),
        ("a\rb", "a\\rb", "a\\rb"),
        ("a\x00b", "a\\x00b", "a\\x00b"),
        ("a\x01b", "a\\x01b", "a\\x01b"),
        ("a\x1fb", "a\\x1fb", "a\\x1fb"),
        ("a\x7fb", "a\\x7fb", "a\\x7fb"),
        ("it's", "it\\'s", "it\\'s"),
        ("a\\b", "a\\\\b", "a\\\\b"),
        ("$VAR", "\\$VAR", "$VAR"),
        ("`cmd`", "\\`cmd\\`", "`cmd`"),
        ("0\x1f", "0\\x1f", "0\\x1f"),
    ],
)
def test_escape_consistency(input_value, expected_bash, expected_fish):
    assert _escape_with_ansi_c(input_value) == expected_bash
    assert _escape_with_hex(input_value) == expected_fish


@pytest.mark.parametrize(
    ("shell_name", "expected"),
    [
        ("bash", ShellType.BASH),
        ("zsh", ShellType.ZSH),
        ("fish", ShellType.FISH),
        ("BASH", ShellType.BASH),
        ("/bin/bash", ShellType.BASH),
        ("/usr/bin/zsh", ShellType.ZSH),
        ("bash-5.1", ShellType.BASH),
        ("zsh-5.8", ShellType.ZSH),
        ("sh", ShellType.UNKNOWN),
        ("python", ShellType.UNKNOWN),
        ("", ShellType.UNKNOWN),
        ("unknown-shell", ShellType.UNKNOWN),
    ],
    ids=[
        "bash-lowercase",
        "zsh-lowercase",
        "fish-lowercase",
        "bash-uppercase",
        "bash-with-path",
        "zsh-with-path",
        "bash-with-version",
        "zsh-with-version",
        "sh-not-recognized",
        "python-not-shell",
        "empty-string",
        "unknown-shell",
    ],
)
def test_parse_shell_name(shell_name, expected):
    assert _parse_shell_name(shell_name) == expected


@pytest.mark.parametrize(
    ("shell_env", "expected", "delete_env"),
    [
        ("/bin/bash", ShellType.BASH, False),
        ("/usr/bin/zsh", ShellType.ZSH, False),
        ("/usr/local/bin/fish", ShellType.FISH, False),
        ("/bin/sh", ShellType.UNKNOWN, False),
        ("", ShellType.UNKNOWN, False),
        (None, ShellType.UNKNOWN, True),
    ],
    ids=[
        "bash-env",
        "zsh-env",
        "fish-env",
        "unknown-shell-env",
        "empty-shell-env",
        "no-shell-env",
    ],
)
def test_detect_shell_from_environment(monkeypatch, shell_env, expected, delete_env):
    monkeypatch.setattr("schemathesis.core.shell._DETECTED_SHELL", None)
    if delete_env:
        monkeypatch.delenv("SHELL", raising=False)
    else:
        monkeypatch.setenv("SHELL", shell_env)
    assert detect_shell() == expected
