from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import click

from schemathesis.cli.core import get_terminal_width
from schemathesis.cli.output import make_console, make_progress_bar
from schemathesis.core import storage

if TYPE_CHECKING:
    from rich.console import Console

    from schemathesis.cli.commands.replay.executor import ReplayOutcome
    from schemathesis.reporting.crashes import CrashFile
    from schemathesis.schemas import BaseSchema


@click.command(  # type: ignore[untyped-decorator]
    name="replay",
    short_help="Replay stored crash files",
    # The root group disables help options; re-enable them like the other subcommands do.
    context_settings={"terminal_width": get_terminal_width(), "help_option_names": ["-h", "--help"]},
)
@click.argument("path", default=None, required=False, type=click.Path())  # type: ignore[untyped-decorator]
@click.option("--url", "base_url", default=None, help="Override base URL for replay.")  # type: ignore[untyped-decorator]
@click.option(  # type: ignore[untyped-decorator]
    "--schema-location", default=None, help="Override schema location for loading."
)
@click.option(  # type: ignore[untyped-decorator]
    "--keep", is_flag=True, default=False, help="Retain fixed crashes instead of removing them."
)
@click.pass_context  # type: ignore[untyped-decorator]
def replay(ctx: click.Context, path: str | None, base_url: str | None, schema_location: str | None, keep: bool) -> None:
    """Replay stored crash files."""
    if path is None:
        units = [
            (directory, files, str(directory))
            for directory in _all_crash_dirs(ctx)
            if (files := _crash_files_in(directory))
        ]
        if not units:
            click.echo("No crash files found.")
            return
    else:
        target = Path(path)
        if target.is_file():
            units = [(target.parent, [target], str(target))]
        elif target.is_dir():
            files = _crash_files_in(target)
            if not files:
                click.echo("No crash files found.")
                return
            units = [(target, files, str(target))]
        elif not target.suffix:
            found = _find_by_case_id(ctx, path)
            if found is None:
                click.echo(f"Error: no crash file found for case ID: {path}", err=True)
                sys.exit(2)
            units = [(found[0], found[1], str(target))]
        else:
            click.echo(f"Error: path not found: {target}", err=True)
            sys.exit(2)

    console = make_console()
    has_failing_or_changed = False
    has_error = False
    for crash_dir, crash_files, source in units:
        failing, error, interrupted = _replay_directory(
            ctx,
            crash_dir,
            crash_files,
            base_url=base_url,
            schema_location=schema_location,
            keep=keep,
            console=console,
            source=source,
        )
        has_failing_or_changed = has_failing_or_changed or failing
        has_error = has_error or error
        if interrupted:
            sys.exit(1)

    if has_failing_or_changed:
        sys.exit(1)
    if has_error:
        sys.exit(2)


def _replay_directory(
    ctx: click.Context,
    crash_dir: Path,
    crash_files: list[Path],
    *,
    base_url: str | None,
    schema_location: str | None,
    keep: bool,
    console: Console,
    source: str,
) -> tuple[bool, bool, bool]:
    """Replay one project's crash directory; returns (has_failing_or_changed, has_error, interrupted)."""
    from schemathesis.cli.commands.replay.executor import ReplayStatus, replay_crash_file
    from schemathesis.cli.commands.replay.output import render_replay
    from schemathesis.reporting.crashes import CrashFile, CrashWriter, load_manifest

    crashes: list[CrashFile] = []
    loaded_paths: list[Path] = []
    incompatible: list[Path] = []
    for crash_path in crash_files:
        try:
            crashes.append(CrashFile.from_dict(json.loads(crash_path.read_text())))
            loaded_paths.append(crash_path)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            incompatible.append(crash_path)
    crash_files = loaded_paths

    manifest = load_manifest(crash_dir)
    effective_schema_location = schema_location or (manifest.schema_location if manifest is not None else None)
    # File-based schemas carry no base URL on their operations; fall back to the manifest's recorded base URL
    # for the actual requests when no --url override is given (the header still flags only an explicit override).
    effective_base_url = base_url or (manifest.base_url if manifest is not None else None)
    if not effective_schema_location:
        click.echo("Error: cannot replay without a schema. Pass --schema-location <url-or-path>.", err=True)
        return False, True, False

    schema = _load_schema(ctx, effective_schema_location)
    if schema is None:
        click.echo(f"Error: failed to load schema from {effective_schema_location}", err=True)
        return False, True, False

    project_config = ctx.obj.projects.get(schema.raw_schema)

    # One case that failed several checks is stored as one file per check; replay it once and verify all checks.
    units = _merge_by_case(crashes, crash_files)

    start = time.monotonic()
    outcomes: list[ReplayOutcome] = []
    interrupted = False
    progress = make_progress_bar(console)
    try:
        with progress:
            task = progress.add_task("Replaying", total=len(units))
            for unit in units:
                outcomes.append(
                    replay_crash_file(
                        unit.crash, base_url=effective_base_url, project_config=project_config, schema=schema
                    )
                )
                progress.advance(task)
    except KeyboardInterrupt:
        interrupted = True
        units = units[: len(outcomes)]

    duration_ms = int((time.monotonic() - start) * 1000)

    merged_crashes = [unit.crash for unit in units]

    # Incompatible files are reported but never deleted: a matching Schemathesis version may still reproduce them.
    removal_count = 0
    files_to_remove: set[str] = set()
    if not keep and not interrupted:
        for unit, outcome in zip(units, outcomes, strict=True):
            fixed = {check.name for check in outcome.check_outcomes if check.status is ReplayStatus.FIXED}
            for path, check_name in unit.sources:
                if check_name in fixed:
                    files_to_remove.add(path.name)
        removal_count = len(files_to_remove)

    if outcomes or incompatible:
        render_replay(
            crashes=merged_crashes,
            outcomes=outcomes,
            source=source,
            base_url=base_url,
            duration_ms=duration_ms,
            output_config=ctx.obj.output,
            removal_count=removal_count,
            incompatible_count=len(incompatible),
            interrupted=interrupted,
        )

    if files_to_remove:
        CrashWriter(directory=crash_dir).remove_files(files_to_remove)

    failing = any(outcome.status is ReplayStatus.FAILED for outcome in outcomes)
    error = any(outcome.status is ReplayStatus.ERRORED for outcome in outcomes)
    return failing, error, interrupted


@dataclass(slots=True)
class _ReplayUnit:
    crash: CrashFile
    # Source files contributing to this case, each paired with its single recorded check name.
    sources: list[tuple[Path, str]]


def _merge_by_case(crashes: list[CrashFile], paths: list[Path]) -> list[_ReplayUnit]:
    """Group crash files sharing a case into one unit: same request sequence, union of recorded checks."""
    units: dict[str, _ReplayUnit] = {}
    for crash, path in zip(crashes, paths, strict=True):
        terminal = crash.sequence[-1]
        check_name = terminal.checks[0].name if terminal.checks else ""
        key = crash.case_id or f"\x00{path.name}"
        unit = units.get(key)
        if unit is None:
            units[key] = _ReplayUnit(crash=crash, sources=[(path, check_name)])
        else:
            merged_checks = [*unit.crash.sequence[-1].checks, *terminal.checks]
            merged_terminal = replace(unit.crash.sequence[-1], checks=merged_checks)
            unit.crash = replace(unit.crash, sequence=[*unit.crash.sequence[:-1], merged_terminal])
            unit.sources.append((path, check_name))
    return list(units.values())


def _all_crash_dirs(ctx: click.Context) -> list[Path]:
    cache_directory = ctx.obj.cache.directory
    if cache_directory is not None:
        return [cache_directory / "crashes"]
    if not storage.DEFAULT_ROOT.is_dir():
        return []
    return sorted(p for p in storage.DEFAULT_ROOT.glob("*/cache/crashes") if p.is_dir())


def _crash_files_in(directory: Path) -> list[Path]:
    from schemathesis.reporting.crashes import MANIFEST_FILENAME

    if not directory.is_dir():
        return []
    return sorted(f for f in directory.iterdir() if f.suffix == ".json" and f.name != MANIFEST_FILENAME)


def _load_schema(ctx: click.Context, location: str) -> BaseSchema | None:
    from schemathesis.cli.loaders import load_schema

    project_config = ctx.obj.projects.get_default()
    try:
        return load_schema(location, project_config)
    except Exception:
        return None


def _find_by_case_id(ctx: click.Context, case_id: str) -> tuple[Path, list[Path]] | None:
    # One case that failed several checks is stored as one file per check, all sharing this case_id.
    for crashes_dir in _all_crash_dirs(ctx):
        matches = []
        for crash_path in _crash_files_in(crashes_dir):
            try:
                data = json.loads(crash_path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(data, dict) and data.get("case_id") == case_id:
                matches.append(crash_path)
        if matches:
            return crashes_dir, matches
    return None
