from __future__ import annotations

import difflib
import http.client
import re
import textwrap

import click
import yaml

from schemathesis.cli.commands.explain.explanations import EXPLANATIONS, Example, Explanation
from schemathesis.config._checks import (
    MISSING_REQUIRED_HEADER_EXPECTED_STATUSES,
    NEGATIVE_DATA_REJECTION_EXPECTED_STATUSES,
    NOT_A_SERVER_ERROR_EXPECTED_STATUSES,
    POSITIVE_DATA_ACCEPTANCE_EXPECTED_STATUSES,
)
from schemathesis.core.failures import Severity
from schemathesis.failure_codes import iter_failure_classes

DOCS_URL = "https://schemathesis.readthedocs.io/en/stable/reference/failures/#{anchor}"
# Below this many columns a cut summary says nothing, so `--list` leaves it out.
MIN_SUMMARY_WIDTH = 40
# Prose stays readable on wide terminals.
MAX_PROSE_WIDTH = 100
DEFAULT_EXPECTED_STATUSES = {
    "not_a_server_error": NOT_A_SERVER_ERROR_EXPECTED_STATUSES,
    "positive_data_acceptance": POSITIVE_DATA_ACCEPTANCE_EXPECTED_STATUSES,
    "negative_data_rejection": NEGATIVE_DATA_REJECTION_EXPECTED_STATUSES,
    "missing_required_header": MISSING_REQUIRED_HEADER_EXPECTED_STATUSES,
}
# Severity -> (foreground color, bold).
SEVERITY_STYLES: dict[Severity, tuple[str | None, bool | None]] = {
    Severity.CRITICAL: ("red", True),
    Severity.HIGH: ("red", None),
    Severity.MEDIUM: ("magenta", None),
    Severity.LOW: (None, None),
}
SHORT_SEVERITY = {Severity.CRITICAL: "CRIT", Severity.HIGH: "HIGH", Severity.MEDIUM: "MED", Severity.LOW: "LOW"}
VERIFY_TEXT = (
    "Run `st replay <case id>` with the ID from the `Test Case ID:` line of the failure output. It reruns the "
    "stored case and reports whether it still fails; once it passes, replay deletes the stored case. The replay "
    "needs the run's credentials: run it with the same config, or pass `--header`, `--auth` or `--auth-wfc`."
)


class Styler:
    __slots__ = ("color",)

    def __init__(self, color: bool) -> None:
        self.color = color

    def __call__(
        self,
        text: str,
        *,
        fg: str | None = None,
        bold: bool | None = None,
        dim: bool | None = None,
        underline: bool | None = None,
    ) -> str:
        if not self.color:
            return text
        return click.style(text, fg=fg, bold=bold, dim=dim, underline=underline)

    def prose(self, text: str) -> str:
        return self.prose_lines([text])[0]

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


def render_page(
    code: str,
    explanation: Explanation,
    *,
    kind_title: str,
    severity: Severity,
    wfc: tuple[str, ...],
    width: int,
    color: bool,
) -> str:
    style = Styler(color)
    prose_width = min(width, MAX_PROSE_WIDTH)
    left = f"{code}  {kind_title}"
    label = severity.value.upper()
    gap = max(width - len(left) - len(label), 2)
    lines = [
        style(left, bold=True)
        + " " * gap
        + style(label, fg=SEVERITY_STYLES[severity][0], bold=SEVERITY_STYLES[severity][1])
    ]
    checks = [f"{name} (GraphQL only)" if name in explanation.graphql_only else name for name in explanation.checks]
    meta = f"checks {', '.join(checks) or 'user-defined'}"
    if wfc:
        meta += f"   WFC {', '.join(wfc)}"
    lines += [style(meta, dim=True), ""]
    lines += _reflow(explanation.summary, prose_width, indent="", style=style)
    sections = [
        ("Why it matters", _reflow(explanation.why, prose_width, indent="  ", style=style)),
        ("Example", _example(explanation, style)),
        ("How Schemathesis found it", _reflow(explanation.how_found, prose_width, indent="  ", style=style)),
        ("How to fix", _reflow(explanation.fix, prose_width, indent="  ", style=style)),
        ("False positives", _reflow(explanation.false_positives, prose_width, indent="  ", style=style)),
        ("Verify the fix", _reflow(explanation.verify or VERIFY_TEXT, prose_width, indent="  ", style=style)),
        ("Tuning", _tuning(explanation, prose_width, style)),
        ("Turn it off", _turn_off(code, explanation, prose_width, style)),
    ]
    for heading, body in sections:
        if body:
            lines += ["", style(heading, bold=True), *body]
    url = DOCS_URL.format(anchor=code.lower())
    lines += ["", f"{style('Docs', bold=True)}  {style(url, underline=True)}"]
    return "\n".join(lines)


def prose_blocks(text: str) -> list[str]:
    """Paragraphs and `- ` bullets as single lines; an empty string separates paragraphs."""
    joined: list[str] = []
    for paragraph in re.split(r"\n\s*\n", textwrap.dedent(text).strip()):
        if joined:
            joined.append("")
        items: list[str] = []
        for line in paragraph.splitlines():
            stripped = line.strip()
            if stripped.startswith("- ") or not items:
                items.append(stripped)
            else:
                items[-1] += f" {stripped}"
        joined += items
    return joined


def _reflow(text: str, width: int, *, indent: str, style: Styler) -> list[str]:
    """Rewrap paragraphs and `- ` bullets to `width`; wrapped bullet lines continue two spaces in."""
    lines: list[str] = []
    for joined in prose_blocks(text):
        if not joined:
            lines.append("")
            continue
        bullet = joined.startswith("- ")
        wrapped = textwrap.wrap(
            joined,
            width=width,
            initial_indent=indent,
            subsequent_indent=indent + ("  " if bullet else ""),
            break_long_words=False,
            break_on_hyphens=False,
        )
        lines.extend(style.prose_lines(wrapped))
    return lines


def _example(explanation: Explanation, style: Styler) -> list[str]:
    if explanation.example is not None:
        return _render_example(explanation.example, style)
    scenario = textwrap.dedent(explanation.scenario).strip("\n")
    return [f"  {line}" if line else "" for line in scenario.splitlines()]


def example_schema_text(example: Example) -> str:
    return yaml.safe_dump(example.schema, sort_keys=False, default_flow_style=False).rstrip("\n")


def example_response_text(example: Example) -> str:
    reason = http.client.responses.get(example.status_code, "")
    lines = [f"HTTP/1.1 {example.status_code} {reason}".rstrip()]
    lines += [f"{name}: {value}" for name, value in example.headers.items()]
    lines += ["", example.body]
    return "\n".join(lines)


def _render_example(example: Example, style: Styler) -> list[str]:
    return [
        style("  schema:", dim=True),
        *(f"    {line}" for line in example_schema_text(example).splitlines()),
        style("  response:", dim=True),
        *(f"    {line}" if line else "" for line in example_response_text(example).splitlines()),
    ]


def _tuning(explanation: Explanation, width: int, style: Styler) -> list[str]:
    lines: list[str] = []
    intro = (
        "Set the status codes this check expects, in schemathesis.toml. Your list replaces the default, "
        "so copy it and add your status. For one operation only, put the same key, "
        "`checks.<check>.expected-statuses`, in an `[[operations]]` entry with `include-name`:"
    )
    for name in explanation.tuning:
        lines += _reflow(intro, width, indent="  ", style=style)
        lines += [f"    [checks.{name}]", *_toml_list("expected-statuses", DEFAULT_EXPECTED_STATUSES[name], width)]
    return lines


def _toml_list(key: str, values: list[str], width: int) -> list[str]:
    prefix = f"    {key} = ["
    items = [f'"{value}"' for value in values]
    lines: list[str] = []
    current = prefix
    for index, item in enumerate(items):
        piece = item + ("]" if index == len(items) - 1 else ",")
        candidate = current + ("" if current.endswith("[") else " ") + piece
        if len(candidate) > width and not current.endswith("["):
            lines.append(current)
            current = " " * len(prefix) + piece
        else:
            current = candidate
    lines.append(current)
    return lines


def _turn_off(code: str, explanation: Explanation, width: int, style: Styler) -> list[str]:
    if not explanation.checks:
        return [
            style.prose("  `--exclude-checks <name>`, where `<name>` is the custom check named in the failure,"),
            "  or in schemathesis.toml:",
            "    [checks.<name>]",
            "    enabled = false",
        ]
    if explanation.checks == ("max_response_time",):
        return _reflow(
            "Off unless `--max-response-time` or `checks.max_response_time` is set.", width, indent="  ", style=style
        )
    regular = tuple(name for name in explanation.checks if name not in explanation.graphql_only)
    lines = _disable_block(code, regular, indent="  ", style=style)
    if explanation.graphql_only:
        lines.append("  For GraphQL schemas:")
        lines += _disable_block(code, explanation.graphql_only, indent="    ", style=style)
    return lines


def _disable_block(code: str, checks: tuple[str, ...], *, indent: str, style: Styler) -> list[str]:
    lines = [style.prose(f"{indent}`--exclude-checks {','.join(checks)}`"), f"{indent}or in schemathesis.toml:"]
    for name in checks:
        lines += [f"{indent}  [checks.{name}]", f"{indent}  enabled = false"]
    also = also_disables(code, checks)
    if also:
        lines.append(f"{indent}Also disables:")
        lines += [f"{indent}  {other}  {title}" for other, title in also]
    return lines


def also_disables(code: str, checks: tuple[str, ...]) -> list[tuple[str, str]]:
    """Other codes, with their titles, that turning off `checks` also hides."""
    found = []
    for cls in iter_failure_classes():
        explanation = EXPLANATIONS[cls.code]
        shared = set(explanation.checks) & set(checks)
        if cls.code != code and shared:
            title = cls.kind_title
            if shared <= set(explanation.graphql_only):
                title += " (GraphQL only)"
            found.append((cls.code, title))
    return found


def render_list(*, width: int, color: bool) -> str:
    style = Styler(color)
    classes = iter_failure_classes()
    title_width = max(len(cls.kind_title) for cls in classes)
    lines: list[str] = []
    current_group = ""
    for cls in classes:
        group = GROUPS[cls.code[2]]
        if group != current_group:
            if lines:
                lines.append("")
            lines.append(style(group, bold=True))
            current_group = group
        row = f"  {cls.code}  {SHORT_SEVERITY[cls.severity_default]:<4}  {cls.kind_title:<{title_width}}  "
        summary = " ".join(textwrap.dedent(EXPLANATIONS[cls.code].summary).split())
        room = width - len(row)
        if room < MIN_SUMMARY_WIDTH:
            lines.append(row.rstrip())
            continue
        lines.append(row + textwrap.shorten(summary, width=room, placeholder="..."))
    return "\n".join(lines)


GROUPS = {
    "1": "Response validation (1xx)",
    "2": "Input handling (2xx)",
    "3": "Stateful behavior (3xx)",
    "4": "Security (4xx)",
    "5": "GraphQL (5xx)",
    "9": "Custom checks (9xx)",
}


def _check_names() -> set[str]:
    return {name for explanation in EXPLANATIONS.values() for name in explanation.checks}


def resolve(argument: str) -> tuple[str, str] | None:
    """Map user input to `("code", "ST108")` or `("check", "<name>")`."""
    value = argument.strip()
    upper = value.upper()
    digits = upper.removeprefix("ST")
    if len(digits) == 3 and digits.isdigit():
        code = f"ST{digits}"
        return ("code", code) if code in EXPLANATIONS else None
    lower = value.lower()
    for cls in iter_failure_classes():
        if lower in (cls.slug, cls.__name__.lower()):
            return ("code", cls.code)
    if lower in _check_names():
        return ("check", lower)
    return None


def suggest(argument: str) -> str | None:
    candidates: dict[str, str] = {}
    for cls in iter_failure_classes():
        display = f"{cls.code} ({cls.kind_title})"
        for name in (cls.code, cls.slug, cls.__name__):
            candidates[name.lower()] = display
    for name in _check_names():
        candidates[name] = name
    matches = difflib.get_close_matches(argument.strip().lower(), candidates, n=1)
    return candidates[matches[0]] if matches else None


def render_resolved(resolved: tuple[str, str], *, width: int, color: bool) -> str:
    kind, value = resolved
    if kind == "check":
        return _render_check(value, width=width, color=color)
    cls = next(cls for cls in iter_failure_classes() if cls.code == value)
    return render_page(
        value,
        EXPLANATIONS[value],
        kind_title=cls.kind_title,
        severity=cls.severity_default,
        wfc=cls.wfc,
        width=width,
        color=color,
    )


def _render_check(name: str, *, width: int, color: bool) -> str:
    style = Styler(color)
    lines = [style(f"`{name}` reports these failures:", bold=True)]
    for cls in iter_failure_classes():
        if name in EXPLANATIONS[cls.code].checks:
            lines.append(f"  {cls.code}  {SHORT_SEVERITY[cls.severity_default]:<4}  {cls.kind_title}")
    lines += ["", "Run `st explain <code>` for details."]
    return "\n".join(lines)
