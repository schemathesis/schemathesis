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

# Make the repo-root `tools/` package importable when this script is invoked by file path
# from outside the repository root (e.g. `python /path/to/scripts/coverage/audit.py`).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from schemathesis.generation import GenerationMode
from tools.corpus.io import (
    CORPUS_NAMES,
    CorpusEntry,
    iter_corpus_entries_from_refs,
    iter_corpus_refs,
    iter_corpus_streaming,
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
_SCHEMA_BATCH_SIZE = 32


def _iter_inputs(args: argparse.Namespace) -> Iterator[CorpusEntry]:
    if args.spec is not None:
        yield load_schema_dict(args.spec)
        return
    yield from iter_corpus_streaming(corpus=args.corpus, only=args.only, limit=args.limit)


def _result_path(out_dir: Path, result: SchemaResult) -> Path:
    return out_dir / result.corpus / f"{result.api.replace('/', '__')}.json"


@dataclass(slots=True, frozen=True)
class _CorpusBatch:
    corpus: str
    members: tuple[str, ...]


@dataclass(slots=True)
class _WorkerOutput:
    result: SchemaResult
    target_name: str
    html_link: str | None
    corpus: str
    api: str


def _iter_input_batches(args: argparse.Namespace, *, batch_size: int = _SCHEMA_BATCH_SIZE) -> Iterator[_CorpusBatch]:
    current_corpus: str | None = None
    members: list[str] = []
    for corpus_name, member_name in iter_corpus_refs(corpus=args.corpus, only=args.only, limit=args.limit):
        if current_corpus is None:
            current_corpus = corpus_name
        if corpus_name != current_corpus or len(members) >= batch_size:
            yield _CorpusBatch(corpus=current_corpus, members=tuple(members))
            current_corpus = corpus_name
            members = []
        members.append(member_name)
    if current_corpus is not None and members:
        yield _CorpusBatch(corpus=current_corpus, members=tuple(members))


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
    output = _WorkerOutput(
        result=result,
        target_name=target.name,
        html_link=html_link,
        corpus=entry.corpus,
        api=api_label,
    )
    _print_outcome(output)
    return output


def _process_batch(
    batch: _CorpusBatch,
    *,
    out_dir: Path,
    html_out: Path,
    phase: PhaseName,
    generation_modes: list[GenerationMode],
    fuzzing_max_examples: int,
) -> list[_WorkerOutput]:
    return [
        _process_entry(
            entry,
            out_dir=out_dir,
            html_out=html_out,
            phase=phase,
            generation_modes=generation_modes,
            fuzzing_max_examples=fuzzing_max_examples,
        )
        for entry in iter_corpus_entries_from_refs(batch.corpus, batch.members)
    ]


_MAX_TASKS_PER_CHILD = 50  # recycle workers periodically to release accumulated memory


def _batch_refs(batch: _CorpusBatch) -> Iterator[str]:
    for member in batch.members:
        yield f"{CORPUS_SCHEME}{batch.corpus}/{member}"


def _single_ref_batches(batch: _CorpusBatch) -> Iterator[_CorpusBatch]:
    for member in batch.members:
        yield _CorpusBatch(corpus=batch.corpus, members=(member,))


def _record_crash(ref: str, reason: str, results: list[SchemaResult], *, phase: PhaseName) -> None:
    """Persist a synthetic failure entry so a dead worker doesn't vanish from the summary."""
    corpus, _, name = ref.removeprefix(CORPUS_SCHEME).partition("/")
    api_label = name.removesuffix(".json")
    print(f"[{corpus}] {api_label}\n     CRASH: {reason}", file=sys.stderr)
    results.append(SchemaResult(api=api_label, corpus=corpus, phase=phase.value, errors=[f"worker_crashed: {reason}"]))


def _record_batch_crash(batch: _CorpusBatch, reason: str, results: list[SchemaResult], *, phase: PhaseName) -> None:
    for ref in _batch_refs(batch):
        _record_crash(ref, reason, results, phase=phase)


def _run_pool(
    queue: list[_CorpusBatch],
    *,
    max_workers: int,
    worker_kwargs: dict,
    results: list[SchemaResult],
    crash_sink: list[_CorpusBatch] | None,
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
        in_flight: dict[Future[list[_WorkerOutput]], _CorpusBatch] = {}
        try:
            while len(in_flight) < max_workers and cursor < len(queue):
                batch = queue[cursor]
                cursor += 1
                in_flight[executor.submit(_process_batch, batch, **worker_kwargs)] = batch

            broken = False
            while in_flight and not broken:
                done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for future in done:
                    batch = in_flight.pop(future)
                    try:
                        outputs = future.result()
                    except BrokenProcessPool:
                        broken = True
                        if crash_sink is None:
                            _record_batch_crash(batch, "killed worker in isolation", results, phase=phase)
                        else:
                            crash_sink.extend(_single_ref_batches(batch))
                    except Exception as exc:
                        _record_batch_crash(batch, f"{exc.__class__.__name__}: {exc}", results, phase=phase)
                    else:
                        for output in outputs:
                            results.append(output.result)
                if broken:
                    # the dead pool poisons every still-in-flight future; treat them as suspects.
                    for batch in in_flight.values():
                        if crash_sink is None:
                            _record_batch_crash(batch, "killed worker in isolation", results, phase=phase)
                        else:
                            crash_sink.extend(_single_ref_batches(batch))
                    in_flight.clear()
                else:
                    while len(in_flight) < max_workers and cursor < len(queue):
                        batch = queue[cursor]
                        cursor += 1
                        in_flight[executor.submit(_process_batch, batch, **worker_kwargs)] = batch
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


def _run_parallel(
    batches: Iterator[_CorpusBatch],
    *,
    max_workers: int,
    worker_kwargs: dict,
    results: list[SchemaResult],
) -> None:
    queue = list(batches)
    crash_candidates: list[_CorpusBatch] = []
    _run_pool(
        queue,
        max_workers=max_workers,
        worker_kwargs=worker_kwargs,
        results=results,
        crash_sink=crash_candidates,
    )
    if crash_candidates:
        print(
            f"\nretrying {len(crash_candidates)} crash-suspect refs one at a time to identify killers",
            file=sys.stderr,
        )
        _run_pool(
            crash_candidates,
            max_workers=1,
            worker_kwargs=worker_kwargs,
            results=results,
            crash_sink=None,
        )


def _print_outcome(output: _WorkerOutput) -> None:
    result = output.result
    print(f"[{output.corpus}] {output.api}", file=sys.stderr)
    keywords = (result.statistic or {}).get("keywords") or {}
    examples = (result.statistic or {}).get("examples") or {}
    flag = f" [errors={len(result.errors)}]" if result.errors else ""
    print(
        f"  -> {output.target_name} | ops={result.operations} cases={result.cases_generated} "
        f"kw={keywords.get('full', 0)}/{keywords.get('total', 0)} "
        f"ex={examples.get('seen', 0)}/{examples.get('total', 0)}{flag}",
        file=sys.stderr,
    )
    for error in result.errors:
        print(f"     error: {error}", file=sys.stderr)
    if output.html_link is not None:
        print(f"     report: {output.html_link}", file=sys.stderr)


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

    worker_kwargs = {
        "out_dir": args.out,
        "html_out": html_out,
        "phase": phase,
        "generation_modes": generation_modes,
        "fuzzing_max_examples": args.max_examples,
    }

    try:
        if args.spec is not None or args.workers <= 1:
            for entry in _iter_inputs(args):
                output = _process_entry(entry, **worker_kwargs)
                results.append(output.result)
        else:
            # `spawn` avoids fork-after-threads deadlocks from hypothesis/tracecov import-time threads.
            _run_parallel(
                _iter_input_batches(args),
                max_workers=args.workers,
                worker_kwargs=worker_kwargs,
                results=results,
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
