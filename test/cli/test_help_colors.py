"""Tests for help output coloring logic."""

import sys
from unittest.mock import Mock

import click
import pytest

from schemathesis.cli.ext.groups import should_use_color


@pytest.mark.parametrize(
    ("ctx_color", "argv", "env_no_color", "expected"),
    [
        # Priority 1: ctx.color (highest)
        (True, ["st", "--no-color", "-h"], False, True),
        (False, ["st", "--force-color", "-h"], False, False),
        # Priority 2: --no-color flag
        (None, ["st", "--no-color", "-h"], False, False),
        (None, ["st", "run", "--no-color", "-h"], False, False),
        (None, ["st", "run", "-h", "--no-color"], False, False),
        # Priority 3: --force-color flag
        (None, ["st", "--force-color", "-h"], False, True),
        (None, ["st", "--force-color", "-h"], True, True),  # Overrides NO_COLOR
        # Priority 4: NO_COLOR environment
        (None, ["st", "-h"], True, False),
        # Priority 5: Default (False in non-TTY environments like tests)
        (None, ["st", "-h"], False, False),
    ],
)
def test_should_use_color(monkeypatch, ctx_color, argv, env_no_color, expected):
    """Test should_use_color() with various priority scenarios."""
    if env_no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(sys, "argv", argv)

    ctx = Mock(spec=click.Context)
    ctx.color = ctx_color

    assert should_use_color(ctx) is expected


@pytest.mark.parametrize(
    ("command", "args", "use_env"),
    [
        # Root command
        ("root", ["--no-color", "-h"], False),
        ("root", ["-h"], True),
        # Run command
        ("run", ["run", "--no-color", "-h"], False),
        ("run", ["run", "-h", "--no-color"], False),
        ("run", ["run", "-h"], True),
    ],
)
def test_help_output_no_colors(cli, monkeypatch, command, args, use_env):
    """Test that help output respects color settings."""
    if use_env:
        monkeypatch.setenv("NO_COLOR", "1")

    if command == "root":
        result = cli.run(*args)
    else:
        result = cli.main(*args)

    # Should not contain ANSI escape codes
    assert "\x1b[" not in result.stdout
