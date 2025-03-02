import os
import shutil

import click

from schemathesis.config import SchemathesisConfig


def get_terminal_width() -> int:
    # Some CI/CD providers (e.g. CircleCI) return a (0, 0) terminal size so provide a default
    return shutil.get_terminal_size((80, 24)).columns


def ensure_color(ctx: click.Context, config: SchemathesisConfig) -> None:
    if config.color:
        ctx.color = True
    elif config.color is False or "NO_COLOR" in os.environ:
        ctx.color = False
        os.environ["NO_COLOR"] = "1"
