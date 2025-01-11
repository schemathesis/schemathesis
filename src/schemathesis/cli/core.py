import os
import shutil

import click


def get_terminal_width() -> int:
    # Some CI/CD providers (e.g. CircleCI) return a (0, 0) terminal size so provide a default
    return shutil.get_terminal_size((80, 24)).columns


def ensure_color(ctx: click.Context, no_color: bool, force_color: bool) -> None:
    if force_color:
        ctx.color = True
    elif no_color or "NO_COLOR" in os.environ:
        ctx.color = False
        os.environ["NO_COLOR"] = "1"
