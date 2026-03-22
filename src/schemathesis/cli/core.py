from __future__ import annotations

import os
import shutil

import click


def get_terminal_width() -> int:
    # Some CI/CD providers (e.g. CircleCI) return a (0, 0) terminal size so provide a default
    return shutil.get_terminal_size((80, 24)).columns


def ensure_color(ctx: click.Context, color: bool | None) -> None:
    if color:
        ctx.color = True
    elif color is False or "NO_COLOR" in os.environ:
        ctx.color = False
        os.environ["NO_COLOR"] = "1"


def resolve_color(force_color: bool, no_color: bool, config_color: bool | None) -> bool | None:
    if force_color:
        return True
    if no_color:
        return False
    return config_color
