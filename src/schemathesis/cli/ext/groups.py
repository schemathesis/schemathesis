from __future__ import annotations

import os
import sys
import textwrap
from collections.abc import Callable
from typing import Any

import click

GROUPS: dict[str, OptionGroup] = {}


def should_use_color(ctx: click.Context) -> bool:
    """Determine whether to use colored output in help text.

    Priority (highest to lowest):
    1. ctx.color (if explicitly set via callbacks)
    2. --no-color flag (from command line)
    3. --force-color flag (from command line)
    4. NO_COLOR environment variable
    5. TTY detection (colorize only if stdout is a TTY)
    6. Default (False for non-TTY environments)
    """
    color_setting = getattr(ctx, "color", None)
    if color_setting is not None:
        # Explicit setting via --no-color or --force-color takes precedence
        return bool(color_setting)
    if "--no-color" in sys.argv:
        # Check command line for --no-color flag (handles any order with -h)
        return False
    if "--force-color" in sys.argv:
        # Check command line for --force-color flag (handles any order with -h)
        return True
    if "NO_COLOR" in os.environ:
        # Respect NO_COLOR environment variable (https://no-color.org/)
        return False
    # Default based on TTY detection (matches Click's auto-detection)
    # In test environments (CliRunner), stdout is not a TTY, so colors are disabled
    return sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False


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


OPTION_COLOR = "cyan"
# Use default terminal color for better readability
HELP_COLOR: str | None = None
SECTION_COLOR = "green"


def _colorize_filtering_description(description: str, style_fn: Callable[..., str]) -> str:
    replacements = [
        ("--include-TYPE VALUE", OPTION_COLOR),
        ("Match operations with exact VALUE", HELP_COLOR),
        ("--include-TYPE-regex PATTERN", OPTION_COLOR),
        ("Match operations using regular expression", HELP_COLOR),
        ("--exclude-TYPE VALUE", OPTION_COLOR),
        ("Exclude operations with exact VALUE", HELP_COLOR),
        ("--exclude-TYPE-regex PATTERN", OPTION_COLOR),
        ("Exclude operations using regular expression", HELP_COLOR),
    ]
    for token, color in replacements:
        description = description.replace(token, style_fn(token, fg=color))
    return description


class CommandWithGroupedOptions(click.Command):
    def format_options(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        # Collect options into groups or ungrouped list
        for group in GROUPS.values():
            group.options = []

        use_color = should_use_color(ctx)

        def style(text: str, **kwargs: Any) -> str:
            # Only apply styling if colors are enabled
            if use_color:
                return click.style(text, **kwargs)
            return text

        for param in self.get_params(ctx):
            rv = param.get_help_record(ctx)
            if rv is not None:
                option_repr, message = rv
                if isinstance(param.type, click.Choice):
                    message += (
                        getattr(param.type, "choices_repr", None)
                        or f" [possible values: {', '.join(param.type.choices)}]"
                    )

                styled_option = style(option_repr, fg=OPTION_COLOR, bold=True)
                styled_message = style(message, fg=HELP_COLOR)

                if isinstance(param, GroupedOption) and param.group is not None:
                    option_group = GROUPS.get(param.group)
                    if option_group is not None:
                        option_group.options.append((styled_option, styled_message))
                else:
                    global_group = GROUPS.get("Global options")
                    if global_group is not None:
                        global_group.options.append((styled_option, styled_message))

        groups = sorted(GROUPS.values(), key=lambda g: g.order)
        # Format each group
        for group in groups:
            with formatter.section(style(group.name, fg=SECTION_COLOR, bold=True)):
                if group.description:
                    description = group.description
                    if use_color and group.name == "Filtering options":
                        description = _colorize_filtering_description(description, style)
                    formatter.write(textwrap.indent(description, " " * formatter.current_indent))
                    formatter.write("\n\n")

                if group.options:
                    formatter.write_dl(group.options)


class StyledGroup(click.Group):
    def format_options(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        use_color = should_use_color(ctx)

        def style(text: str, **kwargs: Any) -> str:
            # Only apply styling if colors are enabled
            if use_color:
                return click.style(text, **kwargs)
            return text

        options = []
        for param in self.get_params(ctx):
            rv = param.get_help_record(ctx)
            if rv is not None:
                option_repr, message = rv
                options.append((style(option_repr, fg=OPTION_COLOR, bold=True), style(message, fg=HELP_COLOR)))

        if options:
            with formatter.section(style("Options", fg=SECTION_COLOR, bold=True)):
                formatter.write_dl(options)

        self.format_commands(ctx, formatter)

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        use_color = should_use_color(ctx)

        def style(text: str, **kwargs: Any) -> str:
            # Only apply styling if colors are enabled
            if use_color:
                return click.style(text, **kwargs)
            return text

        commands = []
        for subcommand in self.list_commands(ctx):
            cmd = self.get_command(ctx, subcommand)
            if cmd is None:
                continue
            if cmd.hidden:
                continue

            commands.append((style(subcommand, fg=OPTION_COLOR, bold=True), cmd.get_short_help_str()))

        if commands:
            with formatter.section(style("Commands", fg=SECTION_COLOR, bold=True)):
                formatter.write_dl(commands)


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
