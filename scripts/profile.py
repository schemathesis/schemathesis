"""Schemathesis performance profiler."""

from __future__ import annotations

import re
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

DEFAULT_CLI_PROFILE_FILENAME = "profile_cli.html"


@dataclass
class ProfileRecord:
    method: str
    path: str
    mode: str
    elapsed: float
    html_path: Path

    def avg_ms(self, count: int) -> float:
        return (self.elapsed / count) * 1000 if count > 0 else 0


def _get_profiler() -> Any:
    """Return a new Profiler instance, or exit if pyinstrument is not installed."""
    try:
        from pyinstrument import Profiler
    except ImportError:
        click.echo("pyinstrument is not installed. Run: uv pip install '.[profiling]'", err=True)
        sys.exit(1)
    return Profiler()


@click.command("cli", context_settings={"ignore_unknown_options": True, "allow_extra_args": True})  # type: ignore[untyped-decorator]
@click.option(  # type: ignore[untyped-decorator]
    "--output",
    "output_file",
    default=None,
    help=f"Output file path (default: {DEFAULT_CLI_PROFILE_FILENAME}).",
)
@click.option("--open", "open_result", is_flag=True, default=False, help="Open the result file after profiling.")  # type: ignore[untyped-decorator]
@click.argument("raw_args", nargs=-1, type=click.UNPROCESSED)  # type: ignore[untyped-decorator]
def cli(
    output_file: str | None,
    open_result: bool,
    raw_args: tuple[str, ...],
) -> None:
    """Wrap a schemathesis run invocation with a profiler.

    Pass schemathesis arguments after '--':

        python scripts/profile.py cli -- run schema.yaml --max-examples=5
    """
    args = list(raw_args)
    if args and args[0] == "--":
        args = args[1:]

    if not args:
        raise click.UsageError("No schemathesis arguments provided. Pass them after '--'.")

    output_path = output_file or DEFAULT_CLI_PROFILE_FILENAME
    profiler = _get_profiler()

    from schemathesis.cli import schemathesis
    from schemathesis.generation import hypothesis

    hypothesis.setup()

    click.echo(f"Profiling: st {' '.join(args)}")
    click.echo(f"Output:    {output_path}")
    click.echo()

    with profiler:
        try:
            schemathesis.main(args=args, standalone_mode=False)
        except SystemExit:
            pass
        except Exception as exc:  # noqa: BLE001
            click.echo(f"schemathesis exited with error: {exc}", err=True)

    Path(output_path).write_text(profiler.output_html(), encoding="utf-8")
    click.echo(f"Profile written to: {output_path}")
    if open_result:
        webbrowser.open(Path(output_path).resolve().as_uri())


@click.command("generate")  # type: ignore[untyped-decorator]
@click.argument("schema_path")  # type: ignore[untyped-decorator]
@click.option("--url", "base_url", default=None, help="API base URL (required for file-based schemas)")  # type: ignore[untyped-decorator]
@click.option(  # type: ignore[untyped-decorator]
    "--include-name",
    "include_name",
    multiple=True,
    metavar="METHOD PATH",
    help='Match operations by method + path, e.g. "POST /chat/completions".',
)
@click.option(
    "--max-examples", "count", default=100, show_default=True, help="Maximum number of test cases per API operation"
)  # type: ignore[untyped-decorator]
@click.option(  # type: ignore[untyped-decorator]
    "--mode",
    type=click.Choice(["positive", "negative", "all"]),
    default="all",
    show_default=True,
    help="Test data generation mode",
)
@click.option(  # type: ignore[untyped-decorator]
    "--output-dir",
    "output_dir",
    default="profiles",
    show_default=True,
    help="Directory to write per-operation HTML profiles.",
)
@click.option(  # type: ignore[untyped-decorator]
    "--open",
    "open_result",
    is_flag=True,
    default=False,
    help="Open each profile HTML in the browser after generation.",
)
def generate(
    schema_path: str,
    base_url: str | None,
    include_name: tuple[str, ...],
    count: int,
    mode: str,
    output_dir: str,
    open_result: bool,
) -> None:
    """Profile data generation per operation in isolation.

    SCHEMA_PATH may be a file path or a URL.
    """
    _get_profiler()

    from hypothesis import HealthCheck, Phase, Verbosity, given, seed, settings
    from hypothesis.errors import Unsatisfiable

    import schemathesis
    from schemathesis import Case
    from schemathesis.core.result import Err
    from schemathesis.generation import hypothesis
    from schemathesis.generation.modes import GenerationMode

    hypothesis.setup()

    click.echo(f"Profiling: {schema_path}")
    click.echo(f"Output:    {output_dir}/")
    click.echo()

    if schema_path.lower().startswith(("http://", "https://")):
        schema = schemathesis.openapi.from_url(schema_path)
    else:
        kwargs: dict[str, Any] = {}
        if base_url:
            kwargs["base_url"] = base_url
        schema = schemathesis.openapi.from_path(schema_path, **kwargs)

    if mode == "all":
        modes = list(GenerationMode)
    elif mode == "positive":
        modes = [GenerationMode.POSITIVE]
    else:
        modes = [GenerationMode.NEGATIVE]

    operation_filter: set[str] = set()
    for op in include_name:
        parts = op.strip().split(None, 1)
        if len(parts) == 2:
            operation_filter.add(f"{parts[0].upper()} {parts[1]}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[ProfileRecord] = []

    for result in schema.get_all_operations():
        if isinstance(result, Err):
            click.echo(f"Skipping invalid operation: {result.err()}", err=True)
            continue
        operation = result.ok()
        method = operation.method.upper()
        path = operation.path

        if operation_filter and f"{method} {path}" not in operation_filter:
            continue

        for gen_mode in modes:
            strategy = operation.as_strategy(generation_mode=gen_mode)
            profiler = _get_profiler()
            t0 = time.perf_counter()

            try:
                with profiler:

                    @seed(0)  # type: ignore[untyped-decorator]
                    @given(case=strategy)  # type: ignore[untyped-decorator]
                    @settings(  # type: ignore[untyped-decorator]
                        max_examples=count,
                        phases=[Phase.generate],
                        suppress_health_check=list(HealthCheck),
                        deadline=None,
                        database=None,
                        verbosity=Verbosity.quiet,
                    )
                    def _gen(case: Case) -> None:
                        pass

                    _gen()
            except Unsatisfiable:
                click.echo(f"  {method:7} {path:50} [{gen_mode.value:8}]  UNSATISFIABLE (skipped)", err=True)
                continue

            elapsed = time.perf_counter() - t0
            mode_label = gen_mode.value
            html_path = out_dir / f"{_normalize_path(method)}_{_normalize_path(path)}_{mode_label}.html"
            html_path.write_text(profiler.output_html(), encoding="utf-8")

            record = ProfileRecord(method, path, mode_label, elapsed, html_path)
            records.append(record)
            click.echo(f"  {method:7} {path:50} [{mode_label:8}]  {elapsed:7.2f}s  ({record.avg_ms(count):.0f}ms/case)")

            if open_result:
                webbrowser.open(html_path.resolve().as_uri())

    if not records:
        click.echo("No operations profiled.")
        return

    records.sort(key=lambda r: r.elapsed, reverse=True)

    click.echo()
    click.echo(f"{'METHOD':<7}  {'PATH':<50}  {'MODE':<8}  {'TOTAL':>8}  {'AVG/CASE':>10}  PROFILE")
    click.echo("-" * 100)
    for record in records:
        click.echo(
            f"{record.method:<7}  {record.path:<50}  {record.mode:<8}  "
            f"{record.elapsed:7.2f}s  {record.avg_ms(count):8.0f}ms  {record.html_path}"
        )


def _normalize_path(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text).strip("_")


@click.group()  # type: ignore[untyped-decorator]
def main() -> None:
    """Schemathesis performance profiler."""


main.add_command(cli, name="cli")
main.add_command(generate, name="generate")

if __name__ == "__main__":
    main()
