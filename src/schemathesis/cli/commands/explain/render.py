from __future__ import annotations

import difflib
import textwrap

import click

from schemathesis.cli.commands.explain.explanations import EXPLANATIONS
from schemathesis.config._checks import CheckConfig, ChecksConfig
from schemathesis.core.failures import Failure
from schemathesis.failure_codes import FAILURE_CLASSES, get_failure_class

# With fewer columns left beside the title, `--list` puts the summary on its own lines.
MIN_SUMMARY_WIDTH = 40
# Prose stays readable on wide terminals.
MAX_PROSE_WIDTH = 100


class Styler:
    __slots__ = ("color",)

    def __init__(self, color: bool) -> None:
        self.color = color

    def __call__(self, text: str, *, bold: bool | None = None, dim: bool | None = None) -> str:
        if not self.color:
            return text
        return click.style(text, bold=bold, dim=dim)

    def prose_lines(self, lines: list[str]) -> list[str]:
        """Color inline code; a code span may continue on the next wrapped line."""
        if not self.color:
            return lines
        styled, inside = [], False
        for line in lines:
            parts = line.split("`")
            rendered = []
            for index, part in enumerate(parts):
                if index > 0:
                    inside = not inside
                rendered.append(click.style(part, fg="cyan") if inside and part else part)
            styled.append("".join(rendered))
        return styled


def render_page(code: str, *, width: int, color: bool) -> str:
    explanation = EXPLANATIONS[code]
    style = Styler(color)
    prose_width = min(width, MAX_PROSE_WIDTH)
    checks = _failure_class(code).checks
    lines = [style(f"{code}  {_failure_class(code).kind_title}", bold=True)]
    lines += [style(f"checks {', '.join(checks) or 'user-defined'}", dim=True), ""]
    lines += _reflow(explanation.summary, prose_width, style=style)
    scenario = _verbatim(explanation.scenario)
    if scenario:
        lines += ["", *scenario]
    if explanation.notes:
        lines += ["", *_reflow(explanation.notes, prose_width, style=style)]
    snippet = config_snippet(code, width=width - 2)
    if snippet:
        lines += ["", *(f"  {line}" for line in snippet)]
    lines += ["", *_reflow(turn_off_text(code), prose_width, style=style)]
    return "\n".join(lines)


def config_snippet(code: str, *, width: int) -> list[str]:
    """The `schemathesis.toml` lines a page shows, or nothing when the code has none."""
    explanation = EXPLANATIONS[code]
    block = textwrap.dedent(explanation.block).strip("\n")
    if block:
        return ["# schemathesis.toml", *block.splitlines()]
    if explanation.tuning is None:
        return []
    name, status = explanation.tuning
    defaults = ChecksConfig().get_by_name(name=name)
    assert isinstance(defaults, CheckConfig), name
    statuses = [*defaults.expected_statuses, status]
    lines = ["# schemathesis.toml", "[[operations]]", f'include-name = "{_operation_example(code)}"']
    return lines + _toml_list(f"checks.{name}.expected-statuses", statuses, width)


def turn_off_text(code: str) -> str:
    checks = _failure_class(code).checks
    flag = f"`--exclude-checks {','.join(checks) or '<name>'}` turns it off"
    hidden = _hidden_with(code, checks)
    return f"{flag}, along with {', '.join(hidden)}." if hidden else f"{flag}."


def _reflow(text: str, width: int, *, style: Styler) -> list[str]:
    wrapped = textwrap.wrap(" ".join(text.split()), width=width, break_long_words=False, break_on_hyphens=False)
    return style.prose_lines(wrapped)


def _verbatim(text: str) -> list[str]:
    return [f"  {line}" if line else "" for line in textwrap.dedent(text).strip("\n").splitlines()]


def _failure_class(code: str) -> type[Failure]:
    cls = get_failure_class(code)
    assert cls is not None, code
    return cls


def _operation_example(code: str) -> str:
    return "Query.books" if _failure_class(code).__module__ == "schemathesis.graphql.checks" else "POST /orders"


def _toml_list(key: str, values: list[str], width: int) -> list[str]:
    items = ", ".join(f'"{value}"' for value in values)
    line = f"{key} = [{items}]"
    if len(line) <= width:
        return [line]
    wrapped = textwrap.wrap(items + ",", width=width - 2, break_on_hyphens=False)
    return [f"{key} = [", *(f"  {part}" for part in wrapped), "]"]


def _hidden_with(code: str, checks: tuple[str, ...]) -> list[str]:
    """Other codes that turning off `checks` hides entirely; codes another check still reports are left out."""
    return [cls.code for cls in FAILURE_CLASSES if cls.code != code and cls.checks and set(cls.checks) <= set(checks)]


def render_list(*, width: int, color: bool) -> str:
    style = Styler(color)
    title_width = max(len(cls.kind_title) for cls in FAILURE_CLASSES)
    lines: list[str] = []
    current_group = ""
    for cls in FAILURE_CLASSES:
        group = GROUPS[cls.code[2]]
        if group != current_group:
            if lines:
                lines.append("")
            lines.append(style(group, bold=True))
            current_group = group
        row = f"  {cls.code}  {cls.kind_title:<{title_width}}  "
        summary = EXPLANATIONS[cls.code].summary
        room = width - len(row)
        if room >= MIN_SUMMARY_WIDTH:
            first, *rest = textwrap.wrap(summary, width=room)
            lines.append(row + first)
            lines += [" " * len(row) + line for line in rest]
            continue
        lines.append(row.rstrip())
        indent = " " * len(f"  {cls.code}  ")
        lines += [indent + line for line in textwrap.wrap(summary, width=max(width - len(indent), 20))]
    lines += ["", "Run `st explain <code>` for details."]
    return "\n".join(lines)


GROUPS = {
    "1": "Response validation (ST1xx)",
    "2": "Input handling (ST2xx)",
    "3": "Stateful behavior (ST3xx)",
    "4": "Security (ST4xx)",
    "9": "Custom checks (ST9xx)",
}


def _check_names() -> set[str]:
    return {name for cls in FAILURE_CLASSES for name in cls.checks}


def resolve(argument: str) -> tuple[str, str] | None:
    """Map user input to `("code", "ST108")` or `("check", "<name>")`."""
    value = argument.strip()
    digits = value.upper().removeprefix("ST")
    if len(digits) == 3 and digits.isdigit():
        code = f"ST{digits}"
        return ("code", code) if code in EXPLANATIONS else None
    lower = value.lower()
    if lower in _check_names():
        return ("check", lower)
    return None


def suggest(argument: str) -> str | None:
    candidates = {cls.code.lower(): f"{cls.code} ({cls.kind_title})" for cls in FAILURE_CLASSES}
    candidates |= {name: name for name in _check_names()}
    matches = difflib.get_close_matches(argument.strip().lower(), candidates, n=1)
    return candidates[matches[0]] if matches else None


def render_resolved(resolved: tuple[str, str], *, width: int, color: bool) -> str:
    kind, value = resolved
    if kind == "check":
        return _render_check(value, color=color)
    return render_page(value, width=width, color=color)


def _render_check(name: str, *, color: bool) -> str:
    style = Styler(color)
    lines = [style(f"`{name}` reports these failures:", bold=True)]
    for cls in FAILURE_CLASSES:
        if name in cls.checks:
            lines.append(f"  {cls.code}  {cls.kind_title}")
    lines += ["", "Run `st explain <code>` for details."]
    return "\n".join(lines)
