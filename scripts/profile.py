"""Schemathesis performance profiler."""

from __future__ import annotations

import json
import os
import re
import resource
import sys
import tempfile
import time
import webbrowser
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
from hypothesis.errors import Unsatisfiable

DEFAULT_CLI_PROFILE_FILENAME = "profiles/profile_cli.html"

# Reach the repo-root `tools/` package; scripts/ does not have it on sys.path by default.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import schemathesis  # noqa: E402
import schemathesis.cli  # noqa: E402
from schemathesis.core.result import Err  # noqa: E402
from schemathesis.generation import GenerationMode, hypothesis  # noqa: E402
from schemathesis.generation.drivers import CoverageGenerator  # noqa: E402
from schemathesis.generation.feedback import FeedbackSources  # noqa: E402
from schemathesis.generation.hypothesis.examples import generate_many  # noqa: E402
from tools.corpus.locator import CORPUS_SCHEME, load_schema_dict, parse_corpus_path  # noqa: E402
from tools.stub_call import install_stub_call  # noqa: E402


@dataclass
class ProfileRecord:
    method: str
    path: str
    mode: str
    elapsed: float
    html_path: Path
    cases: int

    def avg_ms(self) -> float:
        return (self.elapsed / self.cases) * 1000 if self.cases > 0 else 0


def _get_profiler() -> Any:
    """Return a new Profiler instance, or exit if pyinstrument is not installed."""
    try:
        from pyinstrument import Profiler
    except ImportError:
        click.echo("pyinstrument is not installed. Run: uv pip install '.[profiling]'", err=True)
        sys.exit(1)
    return Profiler()


def _load_corpus_dict(schema_path: str) -> dict[str, Any]:
    """Load a schema dict from a ``corpus://`` path."""
    try:
        parse_corpus_path(schema_path)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc
    return load_schema_dict(schema_path).schema


def _load_schema(schema_path: str, base_url: str | None) -> Any:
    if schema_path.startswith(CORPUS_SCHEME):
        schema = schemathesis.openapi.from_dict(_load_corpus_dict(schema_path))
    elif schema_path.lower().startswith(("http://", "https://")):
        schema = schemathesis.openapi.from_url(schema_path)
    else:
        schema = schemathesis.openapi.from_path(schema_path)
    if base_url:
        schema.config.update(base_url=base_url)
    return schema


_MOCK_BASE_URL = "http://localhost"


def _resolve_corpus_args(args: list[str]) -> tuple[list[str], list[str]]:
    """Replace any corpus:// arg with a temp file path, injecting --url automatically.

    Returns (updated_args, tmp_paths).
    """
    updated = []
    tmp_paths = []
    for arg in args:
        if arg.startswith(CORPUS_SCHEME):
            schema_dict = _load_corpus_dict(arg)
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
                json.dump(schema_dict, tmp)
                tmp.flush()
                updated.append(tmp.name)
            updated.append(f"--url={_MOCK_BASE_URL}")
            tmp_paths.append(tmp.name)
        else:
            updated.append(arg)
    return updated, tmp_paths


def _iter_operations(schema: Any, include_name: tuple[str, ...]) -> Generator[Any, None, None]:
    # "post /users" selects `POST /users`; entries without a path are ignored.
    names = [
        f"{parts[0].upper()} {parts[1]}"
        for parts in (name.strip().split(None, 1) for name in include_name)
        if len(parts) == 2
    ]
    if names:
        schema = schema.include(name=names)
    for result in schema.get_all_operations():
        if isinstance(result, Err):
            click.echo(f"Skipping invalid operation: {result.err()}", err=True)
            continue
        yield result.ok()


def _normalize_path(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text).strip("_")


@click.command("cli", context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.option(
    "--output",
    "output_file",
    default=None,
    help=f"Output HTML file path (default: {DEFAULT_CLI_PROFILE_FILENAME}).",
)
@click.option("--open", "open_result", is_flag=True, default=False, help="Open the result file after profiling.")
@click.argument("raw_args", nargs=-1, type=click.UNPROCESSED)
def cli(
    output_file: str | None,
    open_result: bool,
    raw_args: tuple[str, ...],
) -> None:
    """Wrap a schemathesis run invocation with a profiler.

    Pass schemathesis arguments after '--':

        python scripts/profile.py cli -- run schema.yaml --max-examples=5

    corpus:// paths are extracted to a temp file and HTTP calls are mocked automatically:

        python scripts/profile.py cli -- run corpus://openapi-3.1/vercel.com/0.0.1.json --max-examples=5
    """
    args = list(raw_args)
    if args and args[0] == "--":
        args = args[1:]

    if not args:
        raise click.UsageError("No schemathesis arguments provided. Pass them after '--'.")

    args, tmp_paths = _resolve_corpus_args(args)
    if tmp_paths:
        install_stub_call()

    output_path = Path(output_file or DEFAULT_CLI_PROFILE_FILENAME)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    profiler = _get_profiler()

    hypothesis.setup()

    click.echo(f"Profiling: st {' '.join(args)}")
    click.echo(f"Output:    {output_path}")
    click.echo()

    try:
        with profiler:
            try:
                schemathesis.cli.schemathesis.main(args=args, standalone_mode=False)
            except SystemExit:
                pass
            except Exception as exc:  # noqa: BLE001
                click.echo(f"schemathesis exited with error: {exc}", err=True)
    finally:
        for tmp in tmp_paths:
            os.unlink(tmp)

    output_path.write_text(profiler.output_html(), encoding="utf-8")
    click.echo(f"Profile written to: {output_path}")
    if open_result:
        webbrowser.open(output_path.resolve().as_uri())


def _operation_profile_options(command: Callable) -> Callable:
    for option in reversed(
        (
            click.argument("schema_path"),
            click.option("--url", "base_url", default=None, help="API base URL (required for file-based schemas)"),
            click.option(
                "--include-name",
                "include_name",
                multiple=True,
                metavar="METHOD PATH",
                help='Match operations by method + path, e.g. "POST /chat/completions".',
            ),
            click.option(
                "--mode",
                type=click.Choice(["positive", "negative", "all"]),
                default="all",
                show_default=True,
                help="Test data generation mode",
            ),
            click.option(
                "--output-dir",
                "output_dir",
                default="profiles",
                show_default=True,
                help="Directory to write per-operation HTML profiles.",
            ),
            click.option(
                "--open",
                "open_result",
                is_flag=True,
                default=False,
                help="Open each profile HTML in the browser after generation.",
            ),
        )
    ):
        command = option(command)
    return command


def _profile_operations(
    *,
    schema_path: str,
    base_url: str | None,
    include_name: tuple[str, ...],
    mode: str,
    output_dir: str,
    open_result: bool,
    phase: str,
    run: Callable[[Any, GenerationMode], int | None],
    describe: Callable[[ProfileRecord], str],
) -> list[ProfileRecord]:
    """Profile `run` per operation and mode; `run` returns the case count, or `None` to skip."""
    _get_profiler()

    hypothesis.setup()

    click.echo(f"Profiling: {schema_path}")
    click.echo(f"Output:    {output_dir}/")
    click.echo()

    schema = _load_schema(schema_path, base_url)
    modes = GenerationMode.from_choice(mode)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[ProfileRecord] = []

    for operation in _iter_operations(schema, include_name):
        method = operation.method.upper()
        path = operation.path

        for gen_mode in modes:
            profiler = _get_profiler()
            t0 = time.perf_counter()
            with profiler:
                cases = run(operation, gen_mode)
            if cases is None:
                continue
            elapsed = time.perf_counter() - t0
            mode_label = gen_mode.value
            html_path = out_dir / f"{_normalize_path(method)}_{_normalize_path(path)}_{phase}_{mode_label}.html"
            html_path.write_text(profiler.output_html(), encoding="utf-8")

            record = ProfileRecord(method, path, mode_label, elapsed, html_path, cases)
            records.append(record)
            click.echo(f"  {method:7} {path:50} [{mode_label:8}]  {elapsed:7.2f}s  ({describe(record)})")

            if open_result:
                webbrowser.open(html_path.resolve().as_uri())

    if not records:
        click.echo("No operations profiled.")
    records.sort(key=lambda r: r.elapsed, reverse=True)
    return records


@click.command("fuzzing")
@_operation_profile_options
@click.option(
    "--max-examples", "count", default=100, show_default=True, help="Maximum number of test cases per API operation"
)
def fuzzing(
    schema_path: str,
    base_url: str | None,
    include_name: tuple[str, ...],
    count: int,
    mode: str,
    output_dir: str,
    open_result: bool,
) -> None:
    """Profile data generation per operation in isolation.

    SCHEMA_PATH may be a file path, a URL, or a corpus:// path:

        python scripts/profile.py fuzzing corpus://openapi-3.0/vercel.com/0.0.1.json
    """

    def run(operation: Any, generation_mode: GenerationMode) -> int | None:
        try:
            generate_many(operation.as_strategy(generation_mode=generation_mode), count)
        except Unsatisfiable:
            click.echo(
                f"  {operation.method.upper():7} {operation.path:50} [{generation_mode.value:8}]  UNSATISFIABLE (skipped)",
                err=True,
            )
            return None
        return count

    records = _profile_operations(
        schema_path=schema_path,
        base_url=base_url,
        include_name=include_name,
        mode=mode,
        output_dir=output_dir,
        open_result=open_result,
        phase="fuzzing",
        run=run,
        describe=lambda record: f"{record.avg_ms():.0f}ms/case",
    )
    if not records:
        return

    click.echo()
    click.echo(f"{'METHOD':<7}  {'PATH':<50}  {'MODE':<8}  {'TOTAL':>8}  {'AVG/CASE':>10}  PROFILE")
    click.echo("-" * 100)
    for record in records:
        click.echo(
            f"{record.method:<7}  {record.path:<50}  {record.mode:<8}  "
            f"{record.elapsed:7.2f}s  {record.avg_ms():8.0f}ms  {record.html_path}"
        )


@click.command("coverage")
@_operation_profile_options
def coverage(
    schema_path: str,
    base_url: str | None,
    include_name: tuple[str, ...],
    mode: str,
    output_dir: str,
    open_result: bool,
) -> None:
    """Profile coverage-phase case generation per operation in isolation.

    SCHEMA_PATH may be a file path, a URL, or a corpus:// path:

        python scripts/profile.py coverage corpus://openapi-3.0/vercel.com/0.0.1.json --mode negative
    """

    def run(operation: Any, generation_mode: GenerationMode) -> int:
        return len(
            list(
                CoverageGenerator(
                    operation=operation,
                    generation_modes=[generation_mode],
                    auth_storage=None,
                    as_strategy_kwargs={},
                    feedback=FeedbackSources(),
                    generation_config=operation.schema.config.generation,
                )
            )
        )

    records = _profile_operations(
        schema_path=schema_path,
        base_url=base_url,
        include_name=include_name,
        mode=mode,
        output_dir=output_dir,
        open_result=open_result,
        phase="coverage",
        run=run,
        describe=lambda record: f"{record.cases} cases",
    )
    if not records:
        return

    click.echo()
    click.echo(f"{'METHOD':<7}  {'PATH':<50}  {'MODE':<8}  {'TOTAL':>8}  {'CASES':>6}  PROFILE")
    click.echo("-" * 100)
    for record in records:
        click.echo(
            f"{record.method:<7}  {record.path:<50}  {record.mode:<8}  "
            f"{record.elapsed:7.2f}s  {record.cases:6}  {record.html_path}"
        )


@click.command("stateful")
@click.argument("schema_path")
@click.option("--url", "base_url", default=None, help="API base URL (required for file-based schemas)")
@click.option(
    "--output",
    "output_file",
    default="profiles/profile_stateful.html",
    show_default=True,
    help="Output HTML file path (time profile).",
)
@click.option("--open", "open_result", is_flag=True, default=False, help="Open the result file after profiling.")
def stateful(schema_path: str, base_url: str | None, output_file: str, open_result: bool) -> None:
    """Profile stateful state-machine construction (link inference + transition collection).

        python scripts/profile.py stateful corpus://openapi-3.0/googleapis.com/compute/alpha.json

    For memory, run under memray: memray run -o out.bin scripts/profile.py stateful corpus://...
    """
    install_stub_call()
    profiler = _get_profiler()

    hypothesis.setup()

    schema = _load_schema(schema_path, base_url or _MOCK_BASE_URL)

    click.echo(f"Profiling: {schema_path}")
    t0 = time.perf_counter()
    with profiler:
        schema.as_state_machine()
    elapsed = time.perf_counter() - t0
    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(profiler.output_html(), encoding="utf-8")

    click.echo(f"  build={elapsed:7.2f}s  peak_rss={peak_rss_mb:.0f}MB")
    click.echo(f"Profile written to: {output_path}")
    if open_result:
        webbrowser.open(output_path.resolve().as_uri())


@click.group()
def main() -> None:
    """Schemathesis performance profiler."""


main.add_command(cli, name="cli")
main.add_command(fuzzing, name="fuzzing")
main.add_command(coverage, name="coverage")
main.add_command(stateful, name="stateful")

if __name__ == "__main__":
    main()
