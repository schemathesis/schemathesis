"""CLI entry: measure schemathesis schema coverage for one schema or a corpus slice."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path

# Make the repo-root `tools/` package importable when this script is invoked by file path
# from outside the repository root (e.g. `python /path/to/scripts/coverage/audit.py`).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from schemathesis.generation import GenerationMode
from tools.corpus.io import CORPUS_NAMES, CorpusEntry, iter_corpus_streaming
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


def _iter_inputs(args: argparse.Namespace) -> Iterator[CorpusEntry]:
    if args.spec is not None:
        yield load_schema_dict(args.spec)
        return
    yield from iter_corpus_streaming(corpus=args.corpus, only=args.only, limit=args.limit)


def _result_path(out_dir: Path, result: SchemaResult) -> Path:
    return out_dir / result.corpus / f"{result.api.replace('/', '__')}.json"


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
    parser.add_argument("--limit", type=int, default=20, help="Cap APIs per corpus (default: 20; 0 = no cap).")
    parser.add_argument("--only", help="Substring filter on corpus entry name.")
    parser.add_argument(
        "--max-examples",
        type=int,
        default=DEFAULT_FUZZING_MAX_EXAMPLES,
        help=f"Hypothesis examples per (operation, mode) for --phase=fuzzing (default: {DEFAULT_FUZZING_MAX_EXAMPLES}).",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="Output dir for per-API JSON and summary.")
    args = parser.parse_args(argv)

    if args.limit == 0:
        args.limit = None

    phase = PhaseName(args.phase)
    generation_modes = GenerationMode.from_choice(args.mode)
    args.out.mkdir(parents=True, exist_ok=True)
    html_out = args.out / "html"

    results: list[SchemaResult] = []
    interrupted = False

    try:
        for entry in _iter_inputs(args):
            api_label = entry.api
            print(f"[{entry.corpus}] {api_label}", file=sys.stderr)
            outcome = audit_schema(
                entry.schema,
                api=api_label,
                corpus=entry.corpus,
                phase=phase,
                generation_modes=generation_modes,
                fuzzing_max_examples=args.max_examples,
            )
            result = outcome.result

            html_link: str | None = None
            keyword_total = ((result.statistic or {}).get("keywords") or {}).get("total", 0)
            if outcome.coverage_map is not None and result.gaps and keyword_total > 0:
                html_path = html_out / entry.corpus / f"{api_label.replace('/', '__')}.html"
                try:
                    html_path.parent.mkdir(parents=True, exist_ok=True)
                    outcome.coverage_map.save_html_report(
                        output_file=str(html_path), title=f"{entry.corpus} / {api_label}"
                    )
                    html_link = f"file://{html_path.resolve()}"
                except Exception as exc:
                    result.errors.append(f"html_report_failed: {exc.__class__.__name__}: {exc}")

            target = _result_path(args.out, result)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w") as fd:
                json.dump(asdict(result), fd, default=str, indent=2)
            results.append(result)

            keywords = (result.statistic or {}).get("keywords") or {}
            # `full` only — matches the tracecov HTML headline; `partial` doesn't count as covered.
            flag = f" [errors={len(result.errors)}]" if result.errors else ""
            print(
                f"  -> {target.name} | ops={result.operations} cases={result.cases_generated} "
                f"kw={keywords.get('full', 0)}/{keywords.get('total', 0)}{flag}",
                file=sys.stderr,
            )
            if html_link is not None:
                print(f"     report: {html_link}", file=sys.stderr)
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
