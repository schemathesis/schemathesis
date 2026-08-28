"""CLI entry: measure schemathesis schema coverage for one schema or a corpus slice."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import signal
import sys
import time
import warnings
from collections import Counter
from collections.abc import Iterator

# Audited corpora carry non-standard regex / format patterns that leak FutureWarnings mid-line into the live progress view
warnings.filterwarnings("ignore", category=FutureWarning)
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypedDict

# Make the repo-root `tools/` package importable when this script is invoked by file path
# from outside the repository root (e.g. `python /path/to/scripts/coverage/audit.py`).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TaskProgressColumn, TextColumn, TimeRemainingColumn
from rich.table import Table

from schemathesis.generation import GenerationMode
from tools.corpus.io import (
    CORPUS_NAMES,
    CorpusEntry,
    iter_corpus_refs,
    iter_corpus_streaming,
    load_corpus_entry,
)
from tools.corpus.locator import CORPUS_SCHEME, load_schema_dict
from tools.coverage.aggregate import aggregate, render_markdown
from tools.coverage.audit import (
    DEFAULT_FUZZING_MAX_EXAMPLES,
    AuditError,
    PhaseName,
    SchemaResult,
    audit_schema,
    error_from_exc,
)
from tools.coverage.caches import clear_internal_caches

DEFAULT_OUT_DIR = Path("out/coverage")
_MODE_CHOICES = ("all", *(mode.value for mode in GenerationMode))
# Recycle workers periodically to release accumulated memory.
_MAX_TASKS_PER_CHILD = 50

# A pending unit of work: the corpus tarball and the member name to audit.
_Ref = tuple[str, str]


def _iter_inputs(args: argparse.Namespace) -> Iterator[CorpusEntry]:
    if args.spec is not None:
        yield load_schema_dict(args.spec)
        return
    yield from iter_corpus_streaming(corpus=args.corpus, only=args.only, limit=args.limit)


def _iter_refs(args: argparse.Namespace) -> Iterator[_Ref]:
    yield from iter_corpus_refs(corpus=args.corpus, only=args.only, limit=args.limit)


def _count_inputs(args: argparse.Namespace) -> int:
    if args.spec is not None:
        return 1
    return sum(1 for _ in iter_corpus_refs(corpus=args.corpus, only=args.only, limit=args.limit))


def _result_path(out_dir: Path, result: SchemaResult) -> Path:
    return out_dir / result.corpus / f"{result.api.replace('/', '__')}.json"


class _WorkerKwargs(TypedDict):
    out_dir: Path
    html_out: Path
    phase: PhaseName
    generation_modes: list[GenerationMode]
    fuzzing_max_examples: int


@dataclass(slots=True)
class _WorkerOutput:
    result: SchemaResult
    target_name: str
    html_link: str | None
    corpus: str
    api: str


def _process_entry(
    entry: CorpusEntry,
    *,
    out_dir: Path,
    html_out: Path,
    phase: PhaseName,
    generation_modes: list[GenerationMode],
    fuzzing_max_examples: int,
) -> _WorkerOutput:
    api_label = entry.api
    outcome = audit_schema(
        entry.schema,
        api=api_label,
        corpus=entry.corpus,
        phase=phase,
        generation_modes=generation_modes,
        fuzzing_max_examples=fuzzing_max_examples,
    )
    result = outcome.result

    html_link: str | None = None
    keyword_total = ((result.statistic or {}).get("keywords") or {}).get("total", 0)
    if outcome.coverage_map is not None and result.gaps and keyword_total > 0 and not _is_complete(result):
        html_path = html_out / entry.corpus / f"{api_label.replace('/', '__')}.html"
        try:
            html_path.parent.mkdir(parents=True, exist_ok=True)
            outcome.coverage_map.save_html_report(output_file=str(html_path), title=f"{entry.corpus} / {api_label}")
            html_link = f"file://{html_path.resolve()}"
        except Exception as exc:
            result.errors.append(error_from_exc("html_report_failed", exc))

    target = _result_path(out_dir, result)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w") as fd:
        json.dump(asdict(result), fd, default=str, indent=2)
    output = _WorkerOutput(
        result=result,
        target_name=target.name,
        html_link=html_link,
        corpus=entry.corpus,
        api=api_label,
    )
    # Prevent schema-derived state from carrying across the worker's task batch.
    del outcome
    clear_internal_caches()
    return output


def _process_ref(
    ref: _Ref,
    *,
    out_dir: Path,
    html_out: Path,
    phase: PhaseName,
    generation_modes: list[GenerationMode],
    fuzzing_max_examples: int,
) -> _WorkerOutput:
    corpus_name, member_name = ref
    entry = load_corpus_entry(corpus_name, member_name)
    return _process_entry(
        entry,
        out_dir=out_dir,
        html_out=html_out,
        phase=phase,
        generation_modes=generation_modes,
        fuzzing_max_examples=fuzzing_max_examples,
    )


def _record_crash(
    ref: _Ref,
    error: AuditError,
    results: list[SchemaResult],
    *,
    phase: PhaseName,
    reporter: _Reporter,
) -> None:
    """Persist a synthetic failure entry so a dead worker doesn't vanish from the summary."""
    corpus_name, member_name = ref
    api_label = member_name.removesuffix(".json")
    result = SchemaResult(api=api_label, corpus=corpus_name, phase=phase.value, errors=[error])
    results.append(result)
    reporter.report(
        _WorkerOutput(result=result, target_name="", html_link=None, corpus=corpus_name, api=api_label),
        crashed=True,
    )


_KILLED_IN_ISOLATION = AuditError(stage="worker_crashed", exception=None, message="killed worker in isolation")


def _run_pool(
    queue: list[_Ref],
    *,
    max_workers: int,
    worker_kwargs: _WorkerKwargs,
    results: list[SchemaResult],
    crash_sink: list[_Ref] | None,
    reporter: _Reporter,
) -> None:
    """Process `queue` with a bounded in-flight window of `max_workers` tasks.

    When the pool dies, the in-flight refs are routed to `crash_sink` for an isolated retry pass.
    Pass `crash_sink=None` to mark crashes as terminal (used by the single-worker isolation pass).
    """
    ctx = multiprocessing.get_context("spawn")
    phase = worker_kwargs["phase"]
    cursor = 0
    while cursor < len(queue):
        # `max_tasks_per_child` exists since 3.11 but typeshed's overloads don't expose it yet.
        executor = ProcessPoolExecutor(  # type: ignore[call-overload]
            max_workers=max_workers, mp_context=ctx, max_tasks_per_child=_MAX_TASKS_PER_CHILD
        )
        in_flight: dict[Future[_WorkerOutput], _Ref] = {}
        try:
            while len(in_flight) < max_workers and cursor < len(queue):
                ref = queue[cursor]
                cursor += 1
                in_flight[executor.submit(_process_ref, ref, **worker_kwargs)] = ref

            broken = False
            while in_flight and not broken:
                done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for future in done:
                    ref = in_flight.pop(future)
                    try:
                        output = future.result()
                    except BrokenProcessPool:
                        broken = True
                        if crash_sink is None:
                            _record_crash(ref, _KILLED_IN_ISOLATION, results, phase=phase, reporter=reporter)
                        else:
                            crash_sink.append(ref)
                    except Exception as exc:
                        _record_crash(
                            ref, error_from_exc("worker_crashed", exc), results, phase=phase, reporter=reporter
                        )
                    else:
                        results.append(output.result)
                        reporter.report(output)
                if broken:
                    # the dead pool poisons every still-in-flight future; treat them as suspects.
                    for ref in in_flight.values():
                        if crash_sink is None:
                            _record_crash(ref, _KILLED_IN_ISOLATION, results, phase=phase, reporter=reporter)
                        else:
                            crash_sink.append(ref)
                    in_flight.clear()
                else:
                    while len(in_flight) < max_workers and cursor < len(queue):
                        ref = queue[cursor]
                        cursor += 1
                        in_flight[executor.submit(_process_ref, ref, **worker_kwargs)] = ref
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


def _run_parallel(
    refs: Iterator[_Ref],
    *,
    max_workers: int,
    worker_kwargs: _WorkerKwargs,
    results: list[SchemaResult],
    reporter: _Reporter,
) -> None:
    queue = list(refs)
    crash_candidates: list[_Ref] = []
    _run_pool(
        queue,
        max_workers=max_workers,
        worker_kwargs=worker_kwargs,
        results=results,
        crash_sink=crash_candidates,
        reporter=reporter,
    )
    if crash_candidates:
        reporter.note(
            f"retrying {len(crash_candidates)} crash-suspect refs one at a time to identify killers",
        )
        _run_pool(
            crash_candidates,
            max_workers=1,
            worker_kwargs=worker_kwargs,
            results=results,
            crash_sink=None,
            reporter=reporter,
        )


# Schema-path suffixes for uncovered keywords that schemathesis cannot exercise without user
# intervention (e.g. unknown `format` values like `format: integer`). A schema whose only
# coverage shortfall lives on one of these is considered complete for the live view.
_IRREDUCIBLE_SCHEMA_PATH_SUFFIXES = ("/format",)


def _only_irreducible_keywords(uncovered: list[dict]) -> bool:
    return bool(uncovered) and all(
        (entry.get("schema_path") or "").endswith(_IRREDUCIBLE_SCHEMA_PATH_SUFFIXES) for entry in uncovered
    )


def _is_complete(result: SchemaResult) -> bool:
    """A schema is complete when every measured surface is fully covered. Responses are excluded."""
    # Errors without an operation coordinate (load_failed, stats_failed) leave no measurable surface.
    if any(not error.path or not error.method for error in result.errors):
        return False
    statistic = result.statistic or {}
    keywords = statistic.get("keywords") or {}
    if int(keywords.get("full", 0)) < int(keywords.get("total", 0)) and not _only_irreducible_keywords(
        result.uncovered_keywords
    ):
        return False
    # Errored ops leave dead parameter/example slots that we can't attribute back to subtract.
    if not result.errors:
        parameters = statistic.get("parameters") or {}
        parameter_covered = int(parameters.get("full", 0)) + int(parameters.get("partial", 0))
        if parameter_covered < int(parameters.get("total", 0)):
            return False
        examples = statistic.get("examples") or {}
        if int(examples.get("seen", 0)) < int(examples.get("total", 0)):
            return False
    return True


def _fraction(covered: int, total: int) -> str:
    """Render `covered/total` with a colour matching how close to fully covered it is."""
    if total == 0:
        return f"[dim]{covered}/{total}[/]"
    ratio = covered / total
    if ratio >= 0.9:
        color = "yellow"
    else:
        color = "red"
    return f"[{color}]{covered}/{total}[/]"


def _coverage_fields(result: SchemaResult) -> list[str]:
    """Return one rendered `<name> N/M` per dimension that isn't fully covered. Responses are excluded."""
    statistic = result.statistic or {}
    keywords = statistic.get("keywords") or {}
    parameters = statistic.get("parameters") or {}
    examples = statistic.get("examples") or {}

    keyword_covered = int(keywords.get("full", 0))
    keyword_total = int(keywords.get("total", 0))
    parameter_covered = int(parameters.get("full", 0)) + int(parameters.get("partial", 0))
    parameter_total = int(parameters.get("total", 0))
    example_seen = int(examples.get("seen", 0))
    example_total = int(examples.get("total", 0))

    fields: list[str] = []
    if keyword_covered < keyword_total:
        fields.append(f"keywords {_fraction(keyword_covered, keyword_total)}")
    if parameter_covered < parameter_total:
        fields.append(f"parameters {_fraction(parameter_covered, parameter_total)}")
    if example_seen < example_total:
        fields.append(f"examples {_fraction(example_seen, example_total)}")
    return fields


class _Reporter:
    """Live stderr renderer: progress bar pinned at the bottom, incomplete schemas printed above."""

    def __init__(self, total: int) -> None:
        self.console = Console(stderr=True)
        self.progress = Progress(
            TextColumn("[bold]Schemas[/]"),
            MofNCompleteColumn(),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            TextColumn("{task.fields[current]}", style="cyan"),
            console=self.console,
            transient=False,
        )
        self.task_id = self.progress.add_task("audit", total=total, current="")

    def __enter__(self) -> _Reporter:
        self.progress.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.progress.stop()

    def report(self, output: _WorkerOutput, *, crashed: bool = False) -> None:
        if crashed or not _is_complete(output.result):
            self._render(output, crashed=crashed)
        self.progress.update(self.task_id, advance=1, current=f"{output.corpus} {output.api}")

    def note(self, message: str) -> None:
        self.console.print(f"[yellow]{message}[/]")

    def _render(self, output: _WorkerOutput, *, crashed: bool) -> None:
        result = output.result
        parts: list[str] = [f"[magenta]\\[{output.corpus}][/] [bold]{output.api}[/]"]
        parts.extend(_coverage_fields(result))
        if crashed:
            parts.append("[red bold]CRASHED[/]")
        elif result.errors:
            parts.append(f"[red]errors {len(result.errors)}[/]")
        if output.html_link is not None:
            parts.append(f"[link={output.html_link}][dim]report[/][/]")
        self.console.print("  ".join(parts))
        for error in result.errors:
            message_head = error.message.splitlines()[0] if error.message else ""
            class_segment = f"{error.exception}: " if error.exception else ""
            self.console.print(f"    [red]![/] {error.stage}: {class_segment}{message_head}")


_RATE_ROWS: tuple[tuple[str, str], ...] = (
    ("operations", "operations"),
    ("keywords (full only)", "keywords_full_only"),
    ("keywords (partial+full)", "keywords_partial_or_full"),
    ("parameters (partial+full)", "parameters_partial_or_full"),
    ("examples", "examples"),
)

_SLOWEST_COUNT = 5
_EXCEPTION_TOP = 10
_RSS_JUMPS_TOP = 10


def _format_bytes(value: int) -> str:
    """Render a signed byte count as KB/MB/GB at the largest fitting unit."""
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    for unit, divisor in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if magnitude >= divisor:
            return f"{sign}{magnitude / divisor:.1f} {unit}"
    return f"{sign}{magnitude} B"


def _format_delta(delta: float, suffix: str = "%") -> str:
    """Render a signed delta with colour: green when up, red when down, dim when zero."""
    if delta > 0.05:
        return f"[green]+{delta:.1f}{suffix}[/]"
    if delta < -0.05:
        return f"[red]{delta:.1f}{suffix}[/]"
    return f"[dim]+0.0{suffix}[/]"


def _format_count_delta(delta: int) -> str:
    if delta > 0:
        return f"[green]+{delta:,}[/]"
    if delta < 0:
        return f"[red]{delta:,}[/]"
    return "[dim]+0[/]"


def _delta_cell(bucket: dict[str, Any], prior: dict[str, Any]) -> str:
    """Render absolute newly-covered count + percentage delta. Show 'totals shifted' when denominators differ."""
    pct_delta = _format_delta(float(bucket["pct"]) - float(prior.get("pct", 0.0)))
    if int(bucket["total"]) != int(prior.get("total", 0)):
        return f"{pct_delta} [dim](totals shifted)[/]"
    covered_delta = int(bucket["covered"]) - int(prior.get("covered", 0))
    return f"{_format_count_delta(covered_delta)} {pct_delta}"


def _finalize(
    results: list[SchemaResult],
    *,
    out_dir: Path,
    baseline: dict[str, Any] | None = None,
    wall_seconds: float = 0.0,
) -> None:
    """Write summary files and print a headline table + path lines to stderr."""
    summary = aggregate(results, wall_seconds=wall_seconds)
    summary_json = out_dir / "summary.json"
    summary_md = out_dir / "summary.md"
    with summary_json.open("w") as fd:
        json.dump(summary, fd, indent=2)
    with summary_md.open("w") as fd:
        fd.write(render_markdown(summary))

    console = Console(stderr=True)
    phase = summary.get("phase") or "unknown"
    baseline_rates = (baseline or {}).get("rates") or {}

    table = Table(title=f"Coverage audit ({phase} phase)", title_style="bold")
    table.add_column("metric")
    table.add_column("covered", justify="right")
    table.add_column("total", justify="right")
    table.add_column("%", justify="right")
    if baseline_rates:
        table.add_column("delta", justify="right")

    rates = summary["rates"]
    for label, key in _RATE_ROWS:
        bucket = rates[key]
        row = [label, f"{bucket['covered']:,}", f"{bucket['total']:,}", f"{bucket['pct']:.1f}%"]
        if baseline_rates:
            prior = baseline_rates.get(key) or {}
            row.append(_delta_cell(bucket, prior))
        table.add_row(*row)

    console.print(table)

    audited = summary["apis_with_results"]
    errored = summary["apis_errored"]
    # "complete" means no remaining gaps AND no errors. Schemas where the only gaps came
    # from errored operations are filtered out of the live view but still count as `errored`.
    complete = sum(1 for result in results if _is_complete(result) and not result.errors)
    partial = max(audited - complete - errored, 0)
    console.print(
        f"APIs: [bold]{audited}[/] audited "
        f"([green]{complete} complete[/], [yellow]{partial} partial[/], [red]{errored} errored[/]) | "
        f"Cases: [bold]{summary['cases_generated']:,}[/] in {summary['wall_seconds']:.1f}s wall "
        f"({summary['duration_seconds']:.1f}s CPU)"
    )
    if summary.get("examples_invalid"):
        console.print(
            f"Excluded [bold]{summary['examples_invalid']:,}[/] inline examples that fail their own schema "
            "(subtracted from the `examples` denominator)."
        )

    slowest = sorted(results, key=lambda r: r.duration_seconds, reverse=True)[:_SLOWEST_COUNT]
    if slowest and slowest[0].duration_seconds > 0:
        title = "Slowest API" if len(slowest) == 1 else f"Slowest {len(slowest)} APIs"
        slow_table = Table(title=title, title_style="bold")
        slow_table.add_column("corpus")
        slow_table.add_column("api")
        slow_table.add_column("seconds", justify="right")
        for result in slowest:
            slow_table.add_row(result.corpus, result.api, f"{result.duration_seconds:.1f}")
        console.print(slow_table)

    top_rss = summary.get("top_rss_jumps") or []
    if top_rss:
        rss_table = Table(title=f"Top {len(top_rss)} RSS jumps", title_style="bold")
        rss_table.add_column("corpus")
        rss_table.add_column("api")
        rss_table.add_column("operation")
        rss_table.add_column("delta", justify="right")
        for entry in top_rss:
            delta = int(entry["delta_bytes"])
            color = "red" if delta > 0 else "green" if delta < 0 else "dim"
            rss_table.add_row(
                entry["corpus"],
                entry["api"],
                entry["operation"],
                f"[{color}]{_format_bytes(delta)}[/]",
            )
        console.print(rss_table)

    exception_counts: Counter[str] = Counter()
    for result in results:
        for error in result.errors:
            if error.exception is not None:
                exception_counts[error.exception] += 1
    if exception_counts:
        exc_table = Table(title="Exception classes", title_style="bold")
        exc_table.add_column("class")
        exc_table.add_column("count", justify="right")
        for cls, count in exception_counts.most_common(_EXCEPTION_TOP):
            exc_table.add_row(cls, f"{count:,}")
        console.print(exc_table)

    console.print(f"output  -> {out_dir}")
    console.print(f"summary -> {summary_json}")
    console.print(f"report  -> {summary_md}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "spec",
        nargs="?",
        help=(
            f"Schema to audit. Accepts {CORPUS_SCHEME}CORPUS/PATH, a file path, or an http(s) URL. "
            "Omit to bulk-audit the bundled corpora."
        ),
    )
    parser.add_argument(
        "--phase",
        choices=[p.value for p in PhaseName],
        default=PhaseName.FUZZING.value,
        help="Generation phase to measure (default: fuzzing).",
    )
    parser.add_argument(
        "--mode",
        choices=_MODE_CHOICES,
        default="all",
        help="Generation modes to drive (default: all).",
    )
    parser.add_argument("--corpus", choices=CORPUS_NAMES, help="Restrict bulk audit to one corpus tarball.")
    parser.add_argument("--limit", type=int, default=0, help="Cap APIs per corpus (0 = no cap, default).")
    parser.add_argument("--only", help="Substring filter on corpus entry name.")
    parser.add_argument(
        "--max-examples",
        type=int,
        default=DEFAULT_FUZZING_MAX_EXAMPLES,
        help=f"Hypothesis examples per (operation, mode) for --phase=fuzzing (default: {DEFAULT_FUZZING_MAX_EXAMPLES}).",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="Output dir for per-API JSON and summary.")
    parser.add_argument(
        "--baseline",
        type=Path,
        help="Path to a prior summary.json to compare against; renders per-metric percentage delta in the summary.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=os.cpu_count() or 1,
        help="Parallel worker processes (default: all cores; 1 disables the process pool).",
    )
    args = parser.parse_args(argv)

    if args.limit == 0:
        args.limit = None

    phase = PhaseName(args.phase)
    generation_modes = GenerationMode.from_choice(args.mode)
    args.out.mkdir(parents=True, exist_ok=True)
    html_out = args.out / "html"

    baseline: dict[str, Any] | None = None
    if args.baseline is not None:
        try:
            with args.baseline.open() as fd:
                baseline = json.load(fd)
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"--baseline {args.baseline}: {exc.__class__.__name__}: {exc}")

    results: list[SchemaResult] = []
    interrupted = False
    wall_started = time.monotonic()

    def _write_summary_and_exit(signum: int, frame: object) -> None:
        print("\ninterrupted; writing summary for completed APIs", file=sys.stderr)
        _finalize(results, out_dir=args.out, baseline=baseline, wall_seconds=time.monotonic() - wall_started)
        os._exit(130)

    signal.signal(signal.SIGINT, _write_summary_and_exit)

    worker_kwargs = _WorkerKwargs(
        out_dir=args.out,
        html_out=html_out,
        phase=phase,
        generation_modes=generation_modes,
        fuzzing_max_examples=args.max_examples,
    )

    total = _count_inputs(args)

    try:
        with _Reporter(total=total) as reporter:
            if args.spec is not None or args.workers <= 1:
                for entry in _iter_inputs(args):
                    output = _process_entry(entry, **worker_kwargs)
                    results.append(output.result)
                    reporter.report(output)
            else:
                # `spawn` avoids fork-after-threads deadlocks from hypothesis/tracecov import-time threads.
                _run_parallel(
                    _iter_refs(args),
                    max_workers=args.workers,
                    worker_kwargs=worker_kwargs,
                    results=results,
                    reporter=reporter,
                )
    except KeyboardInterrupt:
        print("\ninterrupted; writing summary for completed APIs", file=sys.stderr)
        interrupted = True

    _finalize(results, out_dir=args.out, baseline=baseline, wall_seconds=time.monotonic() - wall_started)
    return 130 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
