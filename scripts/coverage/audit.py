"""CLI entry: measure schemathesis schema coverage for one schema or a corpus slice."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import signal
import sys
from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypedDict

# Make the repo-root `tools/` package importable when this script is invoked by file path
# from outside the repository root (e.g. `python /path/to/scripts/coverage/audit.py`).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TaskProgressColumn, TextColumn, TimeRemainingColumn

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
    PhaseName,
    SchemaResult,
    audit_schema,
)

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
    if outcome.coverage_map is not None and result.gaps and keyword_total > 0:
        html_path = html_out / entry.corpus / f"{api_label.replace('/', '__')}.html"
        try:
            html_path.parent.mkdir(parents=True, exist_ok=True)
            outcome.coverage_map.save_html_report(output_file=str(html_path), title=f"{entry.corpus} / {api_label}")
            html_link = f"file://{html_path.resolve()}"
        except Exception as exc:
            result.errors.append(f"html_report_failed: {exc.__class__.__name__}: {exc}")

    target = _result_path(out_dir, result)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w") as fd:
        json.dump(asdict(result), fd, default=str, indent=2)
    return _WorkerOutput(
        result=result,
        target_name=target.name,
        html_link=html_link,
        corpus=entry.corpus,
        api=api_label,
    )


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
    reason: str,
    results: list[SchemaResult],
    *,
    phase: PhaseName,
    reporter: _Reporter,
) -> None:
    """Persist a synthetic failure entry so a dead worker doesn't vanish from the summary."""
    corpus_name, member_name = ref
    api_label = member_name.removesuffix(".json")
    result = SchemaResult(api=api_label, corpus=corpus_name, phase=phase.value, errors=[f"worker_crashed: {reason}"])
    results.append(result)
    reporter.report(
        _WorkerOutput(result=result, target_name="", html_link=None, corpus=corpus_name, api=api_label),
        crashed=True,
    )


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
                            _record_crash(ref, "killed worker in isolation", results, phase=phase, reporter=reporter)
                        else:
                            crash_sink.append(ref)
                    except Exception as exc:
                        _record_crash(ref, f"{exc.__class__.__name__}: {exc}", results, phase=phase, reporter=reporter)
                    else:
                        results.append(output.result)
                        reporter.report(output)
                if broken:
                    # the dead pool poisons every still-in-flight future; treat them as suspects.
                    for ref in in_flight.values():
                        if crash_sink is None:
                            _record_crash(ref, "killed worker in isolation", results, phase=phase, reporter=reporter)
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
    if result.errors:
        return False
    statistic = result.statistic or {}
    keywords = statistic.get("keywords") or {}
    if int(keywords.get("full", 0)) < int(keywords.get("total", 0)) and not _only_irreducible_keywords(
        result.uncovered_keywords
    ):
        return False
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
            first_line = error.splitlines()[0] if error else ""
            self.console.print(f"    [red]![/] {first_line}")


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

    results: list[SchemaResult] = []
    interrupted = False

    def _write_summary_and_exit(signum: int, frame: object) -> None:
        print("\ninterrupted; writing summary for completed APIs", file=sys.stderr)
        summary = aggregate(results)
        summary_json = args.out / "summary.json"
        summary_md = args.out / "summary.md"
        with summary_json.open("w") as fd:
            json.dump(summary, fd, indent=2)
        with summary_md.open("w") as fd:
            fd.write(render_markdown(summary))
        print(f"audited={len(results)} -> {args.out}", file=sys.stderr)
        print(f"summary -> {summary_json}", file=sys.stderr)
        print(f"report  -> {summary_md}", file=sys.stderr)
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

    summary = aggregate(results)
    summary_json = args.out / "summary.json"
    summary_md = args.out / "summary.md"
    with summary_json.open("w") as fd:
        json.dump(summary, fd, indent=2)
    with summary_md.open("w") as fd:
        fd.write(render_markdown(summary))

    print(f"audited={len(results)} -> {args.out}", file=sys.stderr)
    print(f"summary -> {summary_json}", file=sys.stderr)
    print(f"report  -> {summary_md}", file=sys.stderr)
    return 130 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
