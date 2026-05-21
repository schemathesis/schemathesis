from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple
from urllib.parse import urlparse

import click

from schemathesis.cli.commands.replay.executor import CheckOutcome, ReplayOutcome, ReplayStatus, bodies_equal
from schemathesis.cli.core import get_terminal_width
from schemathesis.cli.output import (
    append_replay_command,
    display_section_name,
    failure_formatter,
    format_duration,
    format_summary_banner,
    make_console,
)
from schemathesis.core.failures import format_failures
from schemathesis.core.output import truncate_text
from schemathesis.reporting.crashes import CrashFile, CrashStep

if TYPE_CHECKING:
    from rich.console import Console
    from rich.text import Text

    from schemathesis.config import OutputConfig

GLYPH_FIXED = "+"
GLYPH_FAILING = "x"
GLYPH_CHANGED = "~"
GLYPH_ERROR = "!"
ARROW = "->"


class _Badge(NamedTuple):
    glyph: str
    label: str
    style: str


BADGES: dict[ReplayStatus, _Badge] = {
    ReplayStatus.FIXED: _Badge(GLYPH_FIXED, "FIXED", "bold green"),
    ReplayStatus.FAILED: _Badge(GLYPH_FAILING, "FAILED", "bold red"),
    ReplayStatus.ERRORED: _Badge(GLYPH_ERROR, "ERROR", "bold magenta"),
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

STEP_PATH_MAX = 60
BADGE_WIDTH = max(len(b.glyph) + 1 + len(b.label) for b in BADGES.values())


def render_replay(
    *,
    crashes: list[CrashFile],
    outcomes: list[ReplayOutcome],
    source: str,
    base_url: str | None,
    duration_ms: int,
    output_config: OutputConfig,
    removal_count: int = 0,
    incompatible_count: int = 0,
    interrupted: bool = False,
    console: Console | None = None,
) -> None:
    if console is None:
        console = make_console()
    _print_run_header(console, count=len(crashes), source=source, base_url=base_url)

    # One rollup line per case; multi-line entries set themselves off with a trailing blank.
    trailing_blank = False
    for crash, outcome in zip(crashes, outcomes, strict=True):
        trailing_blank = _print_compact_block(console, crash, outcome)
    # Separate the rollup list from what follows, but only when it printed something
    # (the run header already emits a trailing blank).
    if crashes and not trailing_blank:
        console.print()

    # Group failing checks by the request actually sent, not the declared operation.
    failures: dict[str, list[tuple[CrashFile, ReplayOutcome]]] = {}
    for crash, outcome in zip(crashes, outcomes, strict=True):
        if outcome.failures and outcome.transport_response is not None:
            failures.setdefault(f"{crash.method} {crash.path_template}", []).append((crash, outcome))

    if failures:
        display_section_name("FAILURES")
        for label, group in failures.items():
            display_section_name(label, "_", fg="red")
            for index, (crash, outcome) in enumerate(group, 1):
                if len(crash.sequence) > 1:
                    # Set the step chain off as its own block instead of butting it against header and failure.
                    console.print()
                    _print_step_chain(console, sequence=crash.sequence, outcome=outcome)
                    console.print()
                case_id = f"{index}. Test Case ID: {crash.case_id}" if crash.case_id else None
                reproduce = append_replay_command(crash.code_sample, crash.case_id)
                click.echo(
                    format_failures(
                        case_id=case_id,
                        response=outcome.transport_response,
                        failures=outcome.failures,
                        curl=reproduce,
                        formatter=failure_formatter,
                        config=output_config,
                    ).rstrip()
                )
                click.echo()

    if incompatible_count > 0:
        _print_incompatible_notice(console, incompatible_count)
    if removal_count > 0:
        _print_removal_notice(console, removal_count)
    _print_summary(console, crashes, outcomes, duration_ms=duration_ms, interrupted=interrupted)


def _print_run_header(console: Console, *, count: int, source: str, base_url: str | None) -> None:
    from rich.text import Text

    noun = "case" if count == 1 else "cases"
    line = Text()
    line.append("Replaying ", style="dim")
    line.append(f"{count} {noun}", style="bold")
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


def _print_compact_block(console: Console, crash: CrashFile, outcome: ReplayOutcome) -> bool:
    """Print the rollup line (plus a breakdown for mixed cases); return True if it ends with a blank line."""
    from rich.text import Text

    terminal_step = crash.sequence[-1]
    console.print(_header_line(method=terminal_step.method, path=_step_path(terminal_step), outcome=outcome))

    if not outcome.check_outcomes:
        # A case that could not be evaluated carries a single explanation instead of per-check verdicts.
        if outcome.status is ReplayStatus.ERRORED:
            console.print()
            console.print(Text(f"    {outcome.error_message or 'replay aborted'}", style="magenta"))
            console.print()
            return True
        return False

    # The rollup badge already conveys a uniform verdict; only a mix needs the per-check breakdown.
    statuses = {check.status for check in outcome.check_outcomes}
    if statuses not in ({ReplayStatus.FIXED}, {ReplayStatus.FAILED}):
        console.print()
        _print_check_rows(console, outcome.check_outcomes)
        console.print()
        return True
    return False


def _print_check_rows(console: Console, check_outcomes: list[CheckOutcome]) -> None:
    from rich.text import Text

    # Pad names into a column only when something follows them (a note), so bare rows have no trailing space.
    has_note = any(check.note for check in check_outcomes)
    width = max(len(check.name) for check in check_outcomes) if has_note else 0
    for check in check_outcomes:
        badge = BADGES[check.status]
        row = Text()
        row.append(f"    {badge.glyph} ", style=badge.style)
        row.append(check.name.ljust(width), style=badge.style)
        if check.note:
            row.append("  ")
            row.append(check.note, style="dim")
        console.print(row)


def _header_line(*, method: str, path: str, outcome: ReplayOutcome) -> Text:
    from rich.text import Text

    badge = BADGES[outcome.status]
    line = Text()
    line.append("  ")
    line.append(f"{badge.glyph} {badge.label}".ljust(BADGE_WIDTH), style=badge.style)
    line.append(" ")
    line.append(method, style=METHOD_STYLES.get(method, "bold white"))
    line.append(" ")
    line.append(path, style="bold bright_white")
    return line


def _print_step_chain(
    console: Console,
    *,
    sequence: list[CrashStep],
    outcome: ReplayOutcome,
) -> None:
    from rich.text import Text

    failing_index = len(sequence) - 1
    step_outcomes = outcome.step_outcomes
    # Size each column to the content actually present (paths truncated to a terminal-derived cap),
    # so short methods/paths don't leave a wide gap before the status.
    path_limit = min(STEP_PATH_MAX, max(20, get_terminal_width() - 36))
    paths = [truncate_text(_step_path(step), path_limit) for step in sequence]
    method_column = max(len(step.method) for step in sequence)
    path_column = max(len(path) for path in paths)

    for index, step in enumerate(sequence, start=1):
        actual = step_outcomes[index - 1]

        method = step.method
        path = paths[index - 1]
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
        row.append(method.ljust(method_column), style=METHOD_STYLES.get(method, "bold white"))
        row.append("  ")
        row.append(path.ljust(path_column), style="white")
        row.append("  ")
        # Show a single status when the step reproduced identically; only spell out
        # `recorded -> replayed` when the status actually drifted.
        row.append(str(step.response_status), style=_status_style(step.response_status))
        if actual.status_code != step.response_status:
            row.append(f" {ARROW} ", style="dim")
            row.append(str(actual.status_code), style=_status_style(actual.status_code))
        if delta is not None:
            row.append("  ")
            row.append(GLYPH_CHANGED, style=delta)
        console.print(row)


def _print_incompatible_notice(console: Console, count: int) -> None:
    from rich.text import Text

    noun = "crash file" if count == 1 else "crash files"
    line = Text()
    line.append(f"Skipped {count} incompatible {noun} (could not be read; kept on disk).", style="dim")
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
    console: Console,
    crashes: list[CrashFile],
    outcomes: list[ReplayOutcome],
    *,
    duration_ms: int,
    interrupted: bool = False,
) -> None:
    from rich.text import Text

    counts: dict[ReplayStatus, int] = dict.fromkeys(ReplayStatus, 0)
    for crash, outcome in zip(crashes, outcomes, strict=True):
        if outcome.check_outcomes:
            for check in outcome.check_outcomes:
                counts[check.status] += 1
        else:
            # No per-check verdicts (the case errored before evaluation): attribute its recorded checks.
            counts[outcome.status] += len(crash.sequence[-1].checks) or 1

    parts: list[str] = []
    for status in ReplayStatus:
        if counts[status]:
            parts.append(f"{counts[status]} {status.value}")
    duration = format_duration(duration_ms)
    duration_message = f"interrupted after {duration}" if interrupted else f"in {duration}"
    separator = ", " if interrupted else " "
    message = f"{', '.join(parts)}{separator}{duration_message}" if parts else duration_message

    all_fixed = not interrupted and bool(outcomes) and all(o.status is ReplayStatus.FIXED for o in outcomes)
    style = "bold green" if all_fixed else "bold red"
    banner = format_summary_banner(message, width=get_terminal_width())
    console.print(Text(banner, style=style))


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


def _step_delta(
    *,
    index: int,
    failing_index: int,
    original_status: int,
    actual_status: int,
    original_body: str,
    actual_body: str,
    content_type: str,
) -> str | None:
    """Style for the change glyph on a step row, or `None` when the step reproduced unchanged."""
    changed = original_status != actual_status or not bodies_equal(
        original_body, actual_body, content_type=content_type
    )
    if not changed:
        return None
    return "yellow" if index == failing_index else "dim"
