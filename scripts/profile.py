"""Schemathesis performance profiler."""

from __future__ import annotations

import re
import sys
import time
import webbrowser
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

DEFAULT_CLI_PROFILE_FILENAME = "profiles/profile_cli.html"

# Corpus tooling lives one level up from scripts/
_CORPUS_TOOLS_DIR = Path(__file__).parent.parent / "corpus"
_CORPUS_SCHEME = "corpus://"


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


def _parse_corpus_path(schema_path: str) -> tuple[str, str]:
    """Parse 'corpus://CORPUS_NAME/SCHEMA_NAME' into (corpus_name, schema_name).

    Example: 'corpus://openapi-3.0/vercel.com/0.0.1.json'
      -> corpus_name='openapi-3.0', schema_name='vercel.com/0.0.1.json'
    """
    remainder = schema_path[len(_CORPUS_SCHEME) :]
    corpus_name, _, schema_name = remainder.partition("/")
    if not corpus_name or not schema_name:
        raise click.BadParameter(
            f"corpus:// path must be 'corpus://CORPUS_NAME/SCHEMA_NAME', got: {schema_path!r}",
        )
    return corpus_name, schema_name


def _load_corpus_dict(schema_path: str) -> dict[str, Any]:
    """Load a schema dict from a corpus:// path."""
    import sys

    sys.path.insert(0, str(_CORPUS_TOOLS_DIR))
    from tools import load_from_corpus, read_corpus_file

    corpus_name, schema_name = _parse_corpus_path(schema_path)
    with read_corpus_file(corpus_name) as tar:
        return load_from_corpus(schema_name, tar)


def _load_schema(schema_path: str, base_url: str | None) -> Any:
    import schemathesis

    if schema_path.startswith(_CORPUS_SCHEME):
        schema_dict = _load_corpus_dict(schema_path)
        kwargs: dict[str, Any] = {}
        if base_url:
            kwargs["base_url"] = base_url
        return schemathesis.openapi.from_dict(schema_dict, **kwargs)
    if schema_path.lower().startswith(("http://", "https://")):
        return schemathesis.openapi.from_url(schema_path)
    kwargs = {}
    if base_url:
        kwargs["base_url"] = base_url
    return schemathesis.openapi.from_path(schema_path, **kwargs)


_MOCK_BASE_URL = "http://localhost"


def _install_call_mock() -> None:
    """Patch Case.call so the CLI never makes real HTTP requests."""
    from unittest.mock import patch

    import requests

    from schemathesis.core.transport import Response

    mock_response = Response(
        status_code=200,
        headers={"Content-Type": ["application/json"]},
        content=b"{}",
        request=requests.Request(method="GET", url=f"{_MOCK_BASE_URL}/test").prepare(),
        elapsed=0.1,
        verify=False,
    )
    patch("schemathesis.Case.call", return_value=mock_response).start()


def _resolve_corpus_args(args: list[str]) -> tuple[list[str], list[str]]:
    """Replace any corpus:// arg with a temp file path, injecting --url automatically.

    Returns (updated_args, tmp_paths).
    """
    import json
    import tempfile

    updated = []
    tmp_paths = []
    for arg in args:
        if arg.startswith(_CORPUS_SCHEME):
            schema_dict = _load_corpus_dict(arg)
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            json.dump(schema_dict, tmp)
            tmp.flush()
            tmp.close()
            updated.append(tmp.name)
            updated.append(f"--url={_MOCK_BASE_URL}")
            tmp_paths.append(tmp.name)
        else:
            updated.append(arg)
    return updated, tmp_paths


def _parse_modes(mode: str) -> list[Any]:
    from schemathesis.generation.modes import GenerationMode

    if mode == "all":
        return list(GenerationMode)
    if mode == "positive":
        return [GenerationMode.POSITIVE]
    return [GenerationMode.NEGATIVE]


def _build_operation_filter(include_name: tuple[str, ...]) -> set[str]:
    result: set[str] = set()
    for op in include_name:
        parts = op.strip().split(None, 1)
        if len(parts) == 2:
            result.add(f"{parts[0].upper()} {parts[1]}")
    return result


def _iter_operations(schema: Any, operation_filter: set[str]) -> Generator[Any, None, None]:
    from schemathesis.core.result import Err

    for result in schema.get_all_operations():
        if isinstance(result, Err):
            click.echo(f"Skipping invalid operation: {result.err()}", err=True)
            continue
        operation = result.ok()
        method = operation.method.upper()
        path = operation.path
        if operation_filter and f"{method} {path}" not in operation_filter:
            continue
        yield operation


def _normalize_path(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text).strip("_")


@click.command("cli", context_settings={"ignore_unknown_options": True, "allow_extra_args": True})  # type: ignore[untyped-decorator]
@click.option(  # type: ignore[untyped-decorator]
    "--output",
    "output_file",
    default=None,
    help=f"Output HTML file path (default: {DEFAULT_CLI_PROFILE_FILENAME}).",
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

    corpus:// paths are extracted to a temp file and HTTP calls are mocked automatically:

        python scripts/profile.py cli -- run corpus://openapi-3.1/vercel.com/0.0.1.json --max-examples=5
    """
    import os

    args = list(raw_args)
    if args and args[0] == "--":
        args = args[1:]

    if not args:
        raise click.UsageError("No schemathesis arguments provided. Pass them after '--'.")

    args, tmp_paths = _resolve_corpus_args(args)
    if tmp_paths:
        _install_call_mock()

    output_path = Path(output_file or DEFAULT_CLI_PROFILE_FILENAME)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    profiler = _get_profiler()

    from schemathesis.cli import schemathesis
    from schemathesis.generation import hypothesis

    hypothesis.setup()

    click.echo(f"Profiling: st {' '.join(args)}")
    click.echo(f"Output:    {output_path}")
    click.echo()

    try:
        with profiler:
            try:
                schemathesis.main(args=args, standalone_mode=False)
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


@click.command("fuzzing")  # type: ignore[untyped-decorator]
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

        python scripts/profile.py generate corpus://openapi-3.0/vercel.com/0.0.1.json
    """
    _get_profiler()

    from hypothesis import HealthCheck, Phase, Verbosity, given, seed, settings
    from hypothesis.errors import Unsatisfiable

    from schemathesis import Case
    from schemathesis.generation import hypothesis

    hypothesis.setup()

    click.echo(f"Profiling: {schema_path}")
    click.echo(f"Output:    {output_dir}/")
    click.echo()

    schema = _load_schema(schema_path, base_url)
    modes = _parse_modes(mode)
    operation_filter = _build_operation_filter(include_name)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[ProfileRecord] = []

    for operation in _iter_operations(schema, operation_filter):
        method = operation.method.upper()
        path = operation.path

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
            html_path = out_dir / f"{_normalize_path(method)}_{_normalize_path(path)}_fuzzing_{mode_label}.html"
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


@click.command("coverage")  # type: ignore[untyped-decorator]
@click.argument("schema_path")  # type: ignore[untyped-decorator]
@click.option("--url", "base_url", default=None, help="API base URL (required for file-based schemas)")  # type: ignore[untyped-decorator]
@click.option(  # type: ignore[untyped-decorator]
    "--include-name",
    "include_name",
    multiple=True,
    metavar="METHOD PATH",
    help='Match operations by method + path, e.g. "POST /chat/completions".',
)
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
    _get_profiler()

    from schemathesis.generation import hypothesis
    from schemathesis.generation.hypothesis.builder import generate_coverage_cases

    hypothesis.setup()

    click.echo(f"Profiling: {schema_path}")
    click.echo(f"Output:    {output_dir}/")
    click.echo()

    schema = _load_schema(schema_path, base_url)
    modes = _parse_modes(mode)
    operation_filter = _build_operation_filter(include_name)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[ProfileRecord] = []
    case_counts: dict[Path, int] = {}

    for operation in _iter_operations(schema, operation_filter):
        method = operation.method.upper()
        path = operation.path
        config = operation.schema.config
        phases_cfg = config.phases_for(operation=operation)

        for gen_mode in modes:
            profiler = _get_profiler()
            t0 = time.perf_counter()
            with profiler:
                cases = list(
                    generate_coverage_cases(
                        operation=operation,
                        generation_modes=[gen_mode],
                        auth_storage=None,
                        as_strategy_kwargs={},
                        generate_duplicate_query_parameters=phases_cfg.coverage.generate_duplicate_query_parameters,
                        unexpected_methods=phases_cfg.coverage.unexpected_methods,
                        generation_config=config.generation,
                    )
                )
            elapsed = time.perf_counter() - t0
            case_count = len(cases)
            mode_label = gen_mode.value
            html_path = out_dir / f"{_normalize_path(method)}_{_normalize_path(path)}_coverage_{mode_label}.html"
            html_path.write_text(profiler.output_html(), encoding="utf-8")

            record = ProfileRecord(method, path, mode_label, elapsed, html_path)
            records.append(record)
            case_counts[html_path] = case_count
            click.echo(f"  {method:7} {path:50} [{mode_label:8}]  {elapsed:7.2f}s  ({case_count} cases)")

            if open_result:
                webbrowser.open(html_path.resolve().as_uri())

    if not records:
        click.echo("No operations profiled.")
        return

    records.sort(key=lambda r: r.elapsed, reverse=True)

    click.echo()
    click.echo(f"{'METHOD':<7}  {'PATH':<50}  {'MODE':<8}  {'TOTAL':>8}  {'CASES':>6}  PROFILE")
    click.echo("-" * 100)
    for record in records:
        click.echo(
            f"{record.method:<7}  {record.path:<50}  {record.mode:<8}  "
            f"{record.elapsed:7.2f}s  {case_counts[record.html_path]:6}  {record.html_path}"
        )


@click.group()  # type: ignore[untyped-decorator]
def main() -> None:
    """Schemathesis performance profiler."""


main.add_command(cli, name="cli")
main.add_command(fuzzing, name="fuzzing")
main.add_command(coverage, name="coverage")

if __name__ == "__main__":
    main()
