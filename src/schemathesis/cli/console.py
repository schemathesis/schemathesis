from __future__ import annotations

import os
from typing import Any

from rich.console import Console
from rich.style import Style
from rich.text import Text


def _create_console() -> Console:
    kwargs: dict[str, Any] = {}
    if "PYTEST_VERSION" in os.environ:
        kwargs["width"] = 240
    return Console(**kwargs)


_console = _create_console()


def get_console() -> Console:
    return _console


def echo(message: Any = "", **kwargs: Any) -> None:
    if isinstance(message, str):
        kwargs.setdefault("markup", False)
        _console.print(message, **kwargs)
    else:
        _console.print(message, **kwargs)


def secho(message: str, *, fg: str | None = None, bold: bool = False) -> None:
    style = Style(color=fg, bold=bold) if fg or bold else None
    text = Text(message, style=style)
    _console.print(text)
