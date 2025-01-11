from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

import click

GROUPS: list[str] = []


class CommandWithGroupedOptions(click.Command):
    def format_options(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        groups = defaultdict(list)
        for param in self.get_params(ctx):
            rv = param.get_help_record(ctx)
            if rv is not None:
                (option_repr, message) = rv
                if isinstance(param.type, click.Choice):
                    message += (
                        getattr(param.type, "choices_repr", None)
                        or f" [possible values: {', '.join(param.type.choices)}]"
                    )

                if isinstance(param, GroupedOption):
                    group = param.group
                else:
                    group = "Global options"
                groups[group].append((option_repr, message))
        for group in GROUPS:
            with formatter.section(group or "Options"):
                formatter.write_dl(groups[group], col_max=40)


class GroupedOption(click.Option):
    def __init__(self, *args: Any, group: str | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.group = group


def group(name: str) -> Callable:
    GROUPS.append(name)

    def _inner(cmd: Callable) -> Callable:
        for param in reversed(cmd.__click_params__):  # type: ignore[attr-defined]
            if not isinstance(param, GroupedOption) or param.group is not None:
                break
            param.group = name
        return cmd

    return _inner


def grouped_option(*args: Any, **kwargs: Any) -> Callable:
    kwargs.setdefault("cls", GroupedOption)
    return click.option(*args, **kwargs)
