"""CLI entry: fingerprint coverage-phase output for one schema or a corpus slice, optionally diffing a baseline."""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

# Make the repo-root `tools/` package importable when invoked by file path from outside the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rich.console import Console

from schemathesis.generation import GenerationMode
from tools.corpus.io import CORPUS_NAMES, CorpusEntry, iter_corpus_refs, load_corpus_entry
from tools.corpus.locator import load_schema_dict
from tools.coverage.fingerprint import diff_rows, fingerprint_schema

DEFAULT_OUT_DIR = Path("out/coverage/fingerprints")
# Recycle workers periodically to release accumulated memory.
_MAX_TASKS_PER_CHILD = 50


def _target(out_dir: Path, entry: CorpusEntry) -> Path:
    return out_dir / entry.corpus / f"{entry.api.replace('/', '__')}.json"


def _write(entry: CorpusEntry, out_dir: Path, modes: list[GenerationMode] | None) -> Path:
    fingerprint = fingerprint_schema(entry.schema, generation_modes=modes)
    target = _target(out_dir, entry)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"api": entry.api, "corpus": entry.corpus, "errors": fingerprint.errors, "rows": fingerprint.rows}
    target.write_text(json.dumps(payload, separators=(",", ":")))
    return target


def _process_ref(ref: tuple[str, str], *, out_dir: Path, modes: list[GenerationMode] | None) -> Path:
    corpus, member = ref
    return _write(load_corpus_entry(corpus, member), out_dir, modes)


def _diff(out_dir: Path, baseline_dir: Path, console: Console) -> int:
    differences = 0
    for current_path in sorted(out_dir.rglob("*.json")):
        relative = current_path.relative_to(out_dir)
        baseline_path = baseline_dir / relative
        if not baseline_path.exists():
            console.print(f"{relative}: no baseline")
            differences += 1
            continue
        current = json.loads(current_path.read_text())
        baseline = json.loads(baseline_path.read_text())
        changed = diff_rows(baseline["rows"], current["rows"])
        if not changed:
            continue
        differences += 1
        console.print(f"{relative}: -{len(changed.removed)} +{len(changed.added)}")
        for row in changed.removed:
            console.print("  - " + " | ".join(row))
        for row in changed.added:
            console.print("  + " + " | ".join(row))
    if differences == 0:
        console.print("no differences")
    return 1 if differences else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", help="corpus://CORPUS/NAME, URL, or file path; omit to walk corpus tarballs.")
    parser.add_argument("--corpus", choices=CORPUS_NAMES, help="Restrict the walk to one corpus tarball.")
    parser.add_argument("--only", help="Substring filter on corpus entry name.")
    parser.add_argument("--limit", type=int, default=0, help="Cap APIs per corpus (0 = no cap).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="Output dir for per-API fingerprints.")
    parser.add_argument("--diff", type=Path, help="Baseline dir to compare against after fingerprinting.")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--modes", nargs="+", choices=[mode.value for mode in GenerationMode])
    args = parser.parse_args(argv)
    modes = [GenerationMode(mode) for mode in args.modes] if args.modes else None
    console = Console(markup=False, highlight=False, soft_wrap=True)

    if args.spec is not None:
        _write(load_schema_dict(args.spec), args.out, modes)
    else:
        refs = list(iter_corpus_refs(corpus=args.corpus, only=args.only, limit=args.limit or None))
        # `max_tasks_per_child` exists since 3.11 but typeshed's overloads don't expose it yet.
        # ponytail: a worker killed by a Rust abort / OOM surfaces as BrokenProcessPool and ends the run;
        # rerun with --only to isolate. Per-schema crash isolation like audit.py's can come later if needed.
        executor = ProcessPoolExecutor(  # type: ignore[call-overload]
            max_workers=args.workers, max_tasks_per_child=_MAX_TASKS_PER_CHILD
        )
        with executor:
            work = partial(_process_ref, out_dir=args.out, modes=modes)
            for index, target in enumerate(executor.map(work, refs), 1):
                console.print(f"[{index}/{len(refs)}] {target}")

    if args.diff is not None:
        return _diff(args.out, args.diff, console)
    return 0


if __name__ == "__main__":
    sys.exit(main())
