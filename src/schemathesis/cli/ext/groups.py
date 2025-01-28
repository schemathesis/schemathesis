from __future__ import annotations

import textwrap
from typing import Any, Callable

import click

GROUPS: dict[str, OptionGroup] = {}


class OptionGroup:
    __slots__ = ("order", "name", "description", "options")

    def __init__(
        self,
        name: str,
        *,
        order: int | None = None,
        description: str | None = None,
    ):
        self.name = name
        self.description = description
        self.order = order if order is not None else len(GROUPS) * 100
        self.options: list[tuple[str, str]] = []


class CommandWithGroupedOptions(click.Command):
    def format_options(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        # Collect options into groups or ungrouped list
        for param in self.get_params(ctx):
            rv = param.get_help_record(ctx)
            if rv is not None:
                option_repr, message = rv
                if isinstance(param.type, click.Choice):
                    message += (
                        getattr(param.type, "choices_repr", None)
                        or f" [possible values: {', '.join(param.type.choices)}]"
                    )

                if isinstance(param, GroupedOption) and param.group is not None:
                    group = GROUPS.get(param.group)
                    if group:
                        group.options.append((option_repr, message))
                else:
                    GROUPS["Global options"].options.append((option_repr, message))

        groups = sorted(GROUPS.values(), key=lambda g: g.order)
        # Format each group
        for group in groups:
            with formatter.section(group.name):
                if group.description:
                    formatter.write(textwrap.indent(group.description, " " * formatter.current_indent))
                    formatter.write("\n\n")

                if group.options:
                    formatter.write_dl(group.options)


class GroupedOption(click.Option):
    def __init__(self, *args: Any, group: str | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.group = group


def group(
    name: str,
    *,
    description: str | None = None,
) -> Callable:
    GROUPS[name] = OptionGroup(name, description=description)

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
