from __future__ import annotations

import os
from enum import Enum
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from schemathesis.cli.commands.replay.executor import CheckOutcome, ReplayOutcome, ReplayStatus, bodies_equal
from schemathesis.cli.output import format_duration, format_duration_compact, format_summary_banner, make_console
from schemathesis.core.failures import MessageBlock
from schemathesis.core.output import prepare_response_payload, truncate_text
from schemathesis.reporting.crashes import CrashFile, CrashStep

if TYPE_CHECKING:
    from rich.console import Console
    from rich.text import Text

    from schemathesis.config import OutputConfig

_IS_UTF8 = os.getenv("PYTHONIOENCODING", "utf-8") == "utf-8"

GLYPH_FIXED = "✓" if _IS_UTF8 else "+"
GLYPH_FAILING = "✗" if _IS_UTF8 else "x"
GLYPH_CHANGED = "~"
GLYPH_ERROR = "!"
ARROW = "->"

BADGE_GLYPHS: dict[ReplayStatus, str] = {
    ReplayStatus.FIXED: GLYPH_FIXED,
    ReplayStatus.FAILED: GLYPH_FAILING,
    ReplayStatus.CHANGED: GLYPH_CHANGED,
    ReplayStatus.ERRORED: GLYPH_ERROR,
}
BADGE_LABELS: dict[ReplayStatus, str] = {
    ReplayStatus.FIXED: "FIXED",
    ReplayStatus.FAILED: "FAILED",
    ReplayStatus.CHANGED: "CHANGED",
    ReplayStatus.ERRORED: "ERROR",
}
BADGE_STYLES: dict[ReplayStatus, str] = {
    ReplayStatus.FIXED: "bold green",
    ReplayStatus.FAILED: "bold red",
    ReplayStatus.CHANGED: "bold yellow",
    ReplayStatus.ERRORED: "bold magenta",
}

METHOD_STYLES = {
    "GET": "bold blue",
    "POST": "bold green",
    "DELETE": "bold red",
    "PATCH": "bold yellow",
    "PUT": "bold magenta",
    "HEAD": "bold cyan",
    "OPTIONS": "bold white",
}

DIFF_NOW_STYLES: dict[ReplayStatus, str] = {
    ReplayStatus.FIXED: "green",
    ReplayStatus.FAILED: "dim",
    ReplayStatus.CHANGED: "yellow",
    ReplayStatus.ERRORED: "dim",
}

_DIFF_INDENT = 5
_DIFF_LABEL_WIDTH = 3  # "was" / "now"
_DIFF_STATUS_WIDTH = 3
_DIFF_GAP = 2
DIFF_ROW_PREFIX = _DIFF_INDENT + _DIFF_LABEL_WIDTH + _DIFF_GAP + _DIFF_STATUS_WIDTH + _DIFF_GAP
STEP_PATH_MAX = 60
METHOD_COLUMN = max(len(m) for m in METHOD_STYLES)
CHIP_WIDTH = (
    max(len(g) + 1 + len(label) for g, label in zip(BADGE_GLYPHS.values(), BADGE_LABELS.values(), strict=True)) + 2
)
TIMING_WIDTH = 7
HEADER_FIXED_WIDTH = 1 + METHOD_COLUMN + 1 + 1 + 1 + CHIP_WIDTH + 2 + TIMING_WIDTH


def render_replay(
    *,
    crashes: list[CrashFile],
    outcomes: list[ReplayOutcome],
    source: str,
    base_url: str | None,
    total_checks: int,
    duration_ms: int,
    output_config: OutputConfig,
    removal_count: int = 0,
    incompatible_count: int = 0,
    interrupted: bool = False,
    console: Console | None = None,
) -> None:

    import click

    from schemathesis.cli.output import display_section_name
    from schemathesis.core.failures import format_failures

    if console is None:
        console = make_console()
    _print_run_header(console, count=len(crashes), total_checks=total_checks, source=source, base_url=base_url)

    compact: list[tuple[CrashFile, ReplayOutcome]] = []
    failure_groups: dict[str, list[tuple[CrashFile, ReplayOutcome]]] = {}

    for crash, outcome in zip(crashes, outcomes, strict=True):
        if outcome.failures and outcome.transport_response is not None:
            failure_groups.setdefault(crash.operation, []).append((crash, outcome))
        else:
            compact.append((crash, outcome))

    for crash, outcome in compact:
        _print_compact_block(console, crash, outcome, output_config=output_config)
        console.print()

    for operation, group in failure_groups.items():
        display_section_name(operation, "_", fg="red")
        for index, (crash, outcome) in enumerate(group, 1):
            if len(crash.sequence) > 1:
                _print_step_chain(console, sequence=crash.sequence, outcome=outcome)
            case_id = f"{index}. Test Case ID: {crash.case_id}" if crash.case_id else None
            reproduce = crash.code_sample
            if crash.case_id:
                reproduce += f"\nst replay {crash.case_id}"
            click.echo(
                format_failures(
                    case_id=case_id,
                    response=outcome.transport_response,
                    failures=outcome.failures,
                    curl=reproduce,
                    formatter=_replay_formatter,
                    config=output_config,
                ).rstrip()
            )
            click.echo()

    if incompatible_count > 0:
        _print_incompatible_notice(console, incompatible_count)
    if removal_count > 0:
        _print_removal_notice(console, removal_count)
    _print_summary(console, outcomes, duration_ms=duration_ms, interrupted=interrupted)


def _print_run_header(console: Console, *, count: int, total_checks: int, source: str, base_url: str | None) -> None:
    from rich.text import Text

    cases_noun = "case" if count == 1 else "cases"
    checks_noun = "failed check" if total_checks == 1 else "failed checks"
    line = Text()
    line.append("Replaying ", style="dim")
    line.append(f"{count} {cases_noun}", style="bold")
    line.append(f" ({total_checks} {checks_noun})", style="dim")
    line.append(" from ", style="dim")
    line.append(source, style="cyan")
    console.print(line)
    if base_url:
        console.print()
        override = Text()
        override.append("  Base URL: ", style="bold bright_white")
        override.append(base_url, style="cyan")
        console.print(override)
    console.print()


def _print_compact_block(
    console: Console, crash: CrashFile, outcome: ReplayOutcome, *, output_config: OutputConfig
) -> None:
    from rich.text import Text

    sequence = crash.sequence
    terminal_step = sequence[-1]
    method = terminal_step.method
    path = _step_path(terminal_step)

    console.print(_header_line(console, method=method, path=path, outcome=outcome))

    if outcome.status is ReplayStatus.FIXED:
        return

    if outcome.status is ReplayStatus.ERRORED:
        console.print(Text(f"     {outcome.error_message or 'replay aborted'}", style="magenta"))
        return

    if len(sequence) > 1:
        _print_step_chain(console, sequence=sequence, outcome=outcome)

    if outcome.check_outcomes:
        _print_check_rows(console, outcome.check_outcomes)
        _print_diff_block(console, terminal_step=terminal_step, outcome=outcome, output_config=output_config)

    _print_reproduce(console, crash)


_BODY_INDENT = "    "


def _replay_formatter(block: MessageBlock, content: str) -> str:
    from schemathesis.cli.output import failure_formatter

    return failure_formatter(block, content)


def _print_check_rows(console: Console, check_outcomes: list[CheckOutcome]) -> None:
    from rich.text import Text

    for check in check_outcomes:
        style = BADGE_STYLES[check.status]
        row = Text()
        row.append("- ", style=style)
        row.append(check.name, style=style)
        if check.note:
            row.append("  ")
            row.append(check.note, style="dim")
        console.print(row)
        if check.message:
            console.print()
            for line in check.message.splitlines():
                if line:
                    console.print(Text(f"{_BODY_INDENT}{line}", style="dim"))
                else:
                    console.print()


def _header_line(console: Console, *, method: str, path: str, outcome: ReplayOutcome) -> Text:
    from rich.text import Text

    method_field = method.rjust(METHOD_COLUMN)
    glyph = BADGE_GLYPHS[outcome.status]
    label = BADGE_LABELS[outcome.status]
    chip_inner = f"{glyph} {label}".ljust(CHIP_WIDTH - 2)
    chip = f" {chip_inner} "

    timing = format_duration_compact(outcome.duration_ms)

    width = console.size.width
    path_and_dots = max(0, width - HEADER_FIXED_WIDTH)
    path_len = len(path)
    dots_len = max(0, path_and_dots - path_len)
    truncated = False
    if dots_len < 3:
        path = truncate_text(path, path_and_dots) if path_and_dots > 3 else path[:path_and_dots]
        dots_len = 0
        truncated = True

    line = Text()
    line.append(" ")
    line.append(method_field, style=METHOD_STYLES.get(method, "bold white"))
    line.append(" ")
    line.append(path, style="bold bright_white")
    if not truncated:
        line.append(" ")
        line.append("." * dots_len, style="dim")
    line.append(" ")
    line.append(chip, style=BADGE_STYLES[outcome.status])
    line.append("  ")
    line.append(timing.rjust(TIMING_WIDTH), style="dim")
    return line


def _print_step_chain(
    console: Console,
    *,
    sequence: list[CrashStep],
    outcome: ReplayOutcome,
) -> None:
    from rich.text import Text

    width = console.size.width
    failing_index = len(sequence) - 1
    step_outcomes = outcome.step_outcomes
    column_path = min(STEP_PATH_MAX, max(20, width - 36))

    for index, step in enumerate(sequence, start=1):
        actual = step_outcomes[index - 1]

        method = step.method
        path = _step_path(step)
        delta = _step_delta(
            index=index - 1,
            failing_index=failing_index,
            original_status=step.response_status,
            actual_status=actual.status_code,
            original_body=step.response_body,
            actual_body=actual.body,
            content_type=step.response_headers.get("content-type", ""),
        )

        row = Text()
        row.append("    ")
        row.append(f"{index:>2}", style="dim")
        row.append("  ")
        row.append(method.ljust(METHOD_COLUMN), style=METHOD_STYLES.get(method, "bold white"))
        row.append("  ")
        row.append(truncate_text(path, column_path).ljust(column_path), style="white")
        row.append("  ")
        row.append(str(step.response_status), style=_status_style(step.response_status))
        row.append(f" {ARROW} ", style="dim")
        row.append(str(actual.status_code), style=_status_style(actual.status_code))
        if delta is StepDelta.ACTIVE:
            row.append("  ")
            row.append(GLYPH_CHANGED, style="yellow")
        elif delta is StepDelta.BODY:
            row.append("  ")
            row.append(GLYPH_CHANGED, style="dim")
        console.print(row)


def _print_diff_block(
    console: Console,
    *,
    terminal_step: CrashStep,
    outcome: ReplayOutcome,
    output_config: OutputConfig,
) -> None:
    assert outcome.actual_status is not None

    if outcome.status is ReplayStatus.FAILED:
        _print_response(console, outcome.actual_status, outcome.actual_body or "", output_config)
        return

    _print_response(
        console, terminal_step.response_status, terminal_step.response_body or "", output_config, label="was"
    )
    _print_response(console, outcome.actual_status, outcome.actual_body or "", output_config, label="now")


def _print_response(
    console: Console, status_code: int, body: str, config: OutputConfig, *, label: str | None = None
) -> None:
    import http.client

    from rich.text import Text

    from schemathesis.core.failures import _RFC9110_PHRASES

    reason = _RFC9110_PHRASES.get(status_code) or http.client.responses.get(status_code, "Unknown")
    console.print()
    header = Text()
    if label is not None:
        header.append(f"{label}  ", style="dim" if label == "was" else _status_style(status_code))
    header.append(f"[{status_code}] {reason}:", style=f"bold {_status_style(status_code)}")
    console.print(header)

    payload = prepare_response_payload(body, config=config)
    if payload:
        body_max = max(20, console.size.width - len(_BODY_INDENT) - 2)
        truncated = truncate_text(payload.replace("\n", " "), body_max)
        console.print()
        console.print(Text(f"{_BODY_INDENT}`{truncated}`", style="dim"))


def _print_incompatible_notice(console: Console, count: int) -> None:
    from rich.text import Text

    noun = "crash file" if count == 1 else "crash files"
    line = Text()
    line.append(f"Removed {count} incompatible {noun}.", style="dim")
    console.print(line)
    console.print()


def _print_removal_notice(console: Console, count: int) -> None:
    from rich.text import Text

    noun = "crash file" if count == 1 else "crash files"
    line = Text()
    line.append(f"Removed {count} {noun} (pass --keep to retain).", style="dim")
    console.print(line)
    console.print()


def _print_summary(
    console: Console, outcomes: list[ReplayOutcome], *, duration_ms: int, interrupted: bool = False
) -> None:
    from rich.text import Text

    counts: dict[ReplayStatus, int] = dict.fromkeys(ReplayStatus, 0)
    for outcome in outcomes:
        counts[outcome.status] += 1

    parts: list[str] = []
    for status in ReplayStatus:
        if counts[status]:
            parts.append(f"{counts[status]} {status.value}")
    duration = format_duration(duration_ms)
    duration_message = f"interrupted after {duration}" if interrupted else f"in {duration}"
    separator = ", " if interrupted else " "
    message = f"{', '.join(parts)}{separator}{duration_message}" if parts else duration_message

    all_fixed = not interrupted and all(o.status is ReplayStatus.FIXED for o in outcomes)
    style = "bold green" if all_fixed else "bold red"
    banner = format_summary_banner(message, width=console.size.width)
    console.print(Text(banner, style=style))


def _print_reproduce(console: Console, crash: CrashFile) -> None:
    from rich.text import Text

    lines: list[str] = []
    if crash.code_sample:
        lines.append(crash.code_sample)
    if crash.case_id:
        lines.append(f"st replay {crash.case_id}")
    if not lines:
        return
    console.print()
    header = Text()
    header.append("Reproduce with", style="bold")
    header.append(":")
    console.print(header)
    console.print()
    for line in lines:
        console.print(Text(f"    {line}"))


def _step_path(step: CrashStep) -> str:
    raw = step.url_template or step.url
    return urlparse(raw).path


def _status_style(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "green"
    if 400 <= status_code < 500:
        return "yellow"
    if 500 <= status_code < 600:
        return "red"
    return "white"


class StepDelta(Enum):
    ACTIVE = "active"
    BODY = "body"
    NONE = "none"


def _step_delta(
    *,
    index: int,
    failing_index: int,
    original_status: int,
    actual_status: int,
    original_body: str,
    actual_body: str,
    content_type: str,
) -> StepDelta:
    if index == failing_index:
        if original_status != actual_status or not bodies_equal(original_body, actual_body, content_type=content_type):
            return StepDelta.ACTIVE
        return StepDelta.NONE
    if not bodies_equal(original_body, actual_body, content_type=content_type) or original_status != actual_status:
        return StepDelta.BODY
    return StepDelta.NONE
