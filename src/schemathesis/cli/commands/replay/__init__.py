from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import click

from schemathesis.cli.commands.replay.executor import ReplayOutcome, ReplayStatus, replay_crash_file
from schemathesis.cli.commands.replay.output import render_replay
from schemathesis.cli.output import make_console, make_progress_bar
from schemathesis.core.cache import effective_directory
from schemathesis.reporting.crashes import MANIFEST_FILENAME, CrashFile, CrashWriter, load_manifest
from schemathesis.schemas import BaseSchema


@click.command(name="replay", short_help="Replay stored crash files")  # type: ignore[untyped-decorator]
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
        target = effective_directory(ctx.obj.config.cache.directory, None) / "crashes"
        used_default = True
    else:
        target = Path(path)
        used_default = False

    if target.is_file():
        crash_dir = target.parent
        crash_files = [target]
    elif target.is_dir():
        crash_dir = target
        crash_files = sorted(f for f in target.iterdir() if f.suffix == ".json" and f.name != MANIFEST_FILENAME)
    elif not used_default and path is not None and not Path(path).suffix:
        found = _find_by_case_id(ctx, path)
        if found is not None:
            crash_dir, crash_files = found
        else:
            click.echo(f"Error: no crash file found for case ID: {path}", err=True)
            sys.exit(2)
    elif used_default:
        click.echo("No crash files found.")
        return
    else:
        click.echo(f"Error: path not found: {target}", err=True)
        sys.exit(2)

    if not crash_files:
        click.echo("No crash files found.")
        return

    crashes: list[CrashFile] = []
    loaded_paths: list[Path] = []
    incompatible: list[Path] = []
    for crash_path in crash_files:
        try:
            crashes.append(CrashFile.from_dict(json.loads(crash_path.read_text())))
            loaded_paths.append(crash_path)
        except (KeyError, TypeError, json.JSONDecodeError):
            incompatible.append(crash_path)
    crash_files = loaded_paths

    total_checks = sum(len(step.checks) for crash in crashes for step in crash.sequence)

    manifest = load_manifest(crash_dir)
    effective_schema_location = schema_location or (manifest.schema_location if manifest is not None else None)

    schema = None
    if effective_schema_location:
        schema = _load_schema(ctx, effective_schema_location)

    if schema is not None:
        project_config = ctx.obj.config.projects.get(schema.raw_schema)
    else:
        project_config = ctx.obj.config.projects.get_default()

    from schemathesis.engine.context import make_session

    console = make_console()
    session = make_session(project_config)
    start = time.monotonic()
    outcomes: list[ReplayOutcome] = []

    interrupted = False
    progress = make_progress_bar(console)
    try:
        with progress:
            task = progress.add_task("Replaying", total=len(crashes))
            for data in crashes:
                outcomes.append(replay_crash_file(data, base_url=base_url, session=session, schema=schema))
                progress.advance(task)
    except KeyboardInterrupt:
        interrupted = True
        crashes = crashes[: len(outcomes)]

    duration_ms = int((time.monotonic() - start) * 1000)

    removal_count = 0
    files_to_remove: set[str] = set()
    if not keep and not interrupted:
        for f, outcome in zip(crash_files, outcomes, strict=True):
            if outcome.status is ReplayStatus.FIXED:
                files_to_remove.add(f.name)
        removal_count = len(files_to_remove)
    if not keep:
        for f in incompatible:
            files_to_remove.add(f.name)

    if outcomes or incompatible:
        render_replay(
            crashes=crashes,
            outcomes=outcomes,
            source=str(target),
            base_url=base_url,
            total_checks=total_checks,
            duration_ms=duration_ms,
            output_config=ctx.obj.config.output,
            removal_count=removal_count,
            incompatible_count=len(incompatible),
            interrupted=interrupted,
        )

    if files_to_remove:
        directory = target if target.is_dir() else target.parent
        writer = CrashWriter(directory=directory)
        writer.remove_files(files_to_remove)

    if interrupted:
        sys.exit(1)

    has_failing_or_changed = any(outcome.status in (ReplayStatus.FAILED, ReplayStatus.CHANGED) for outcome in outcomes)
    has_error = any(outcome.status is ReplayStatus.ERRORED for outcome in outcomes)

    if has_failing_or_changed:
        sys.exit(1)
    if has_error:
        sys.exit(2)


def _load_schema(ctx: click.Context, location: str) -> BaseSchema | None:
    from schemathesis.cli.loaders import load_schema

    project_config = ctx.obj.config.projects.get_default()
    try:
        return load_schema(location, project_config)
    except Exception:
        return None


def _find_by_case_id(ctx: click.Context, case_id: str) -> tuple[Path, list[Path]] | None:
    crashes_dir = effective_directory(ctx.obj.config.cache.directory, None) / "crashes"
    if not crashes_dir.is_dir():
        return None
    for crash_path in crashes_dir.iterdir():
        if crash_path.suffix != ".json" or crash_path.name == MANIFEST_FILENAME:
            continue
        try:
            data = json.loads(crash_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("case_id") == case_id:
            return crashes_dir, [crash_path]
    return None
