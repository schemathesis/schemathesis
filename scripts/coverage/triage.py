"""Triage report for `audit.py` output.

Reads coverage JSON(s) produced by the coverage audit and prints a sorted table.
The ranking dimension is selected via `--by`:

- `gaps` (default): rows sorted by absolute keyword shortfall, includes the
  per-location gap breakdown column. Use to find schemas with the worst
  coverage quality.
- `rss`: rows sorted by the largest per-operation RSS jump, includes an
  `rss_gb` column. Use to triage memory blowups in coverage audits. Linux
  only — elsewhere the sampler is unavailable and every row reads `-`.
- `time`: rows sorted by wall-clock duration, includes a `duration_s` column.
  Use to triage slow schemas.

Usage:
    python scripts/coverage/triage.py out/coverage/swagger-2.0/
    python scripts/coverage/triage.py out/coverage/swagger-2.0/ --by rss
    python scripts/coverage/triage.py out/coverage/swagger-2.0/ --by time
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def _stats(data: dict) -> dict:
    stat = data.get("statistic", {})
    return {
        "ops_seen": stat.get("operations", {}).get("seen", 0),
        "ops_total": stat.get("operations", {}).get("total", 0),
        "params_full": stat.get("parameters", {}).get("full", 0),
        "params_total": stat.get("parameters", {}).get("total", 0),
        "kw_full": stat.get("keywords", {}).get("full", 0),
        "kw_total": stat.get("keywords", {}).get("total", 0),
        "kw_missing": stat.get("keywords", {}).get("total", 0) - stat.get("keywords", {}).get("full", 0),
    }


def _gap_breakdown(data: dict) -> str:
    gaps = data.get("gaps", [])
    if not gaps:
        return "-"
    by_loc: Counter[str] = Counter(g.get("location", "?") for g in gaps)
    return ",".join(f"{loc}:{n}" for loc, n in by_loc.most_common())


def _max_rss_jump(data: dict) -> int | None:
    # `None` means the sampler was unavailable (non-Linux); an empty list means no operation ran.
    jumps = data.get("rss_jumps")
    if jumps is None:
        return None
    return max((j.get("delta_bytes", 0) for j in jumps), default=0)


def _row(path: Path, data: dict) -> dict[str, Any]:
    s = _stats(data)
    return {
        "spec": path.stem,
        "api": data.get("api", "?"),
        "ops": f"{s['ops_seen']}/{s['ops_total']}",
        "params_full": f"{s['params_full']}/{s['params_total']}",
        "kw_full": f"{s['kw_full']}/{s['kw_total']}",
        "kw_missing": s["kw_missing"],
        "gaps": _gap_breakdown(data),
        "errors": len(data.get("errors", [])),
        "unsat": len(data.get("unsatisfiable", [])),
        "max_rss_jump_bytes": _max_rss_jump(data),
        "duration_seconds": data.get("duration_seconds", 0.0),
    }


def _iter_files(target: Path) -> Iterator[Path]:
    if target.is_file():
        yield target
        return
    yield from sorted(target.glob("*.json"))


# Sort key, derived column key, derived column header, derived value formatter.
# The first three "context" columns (spec, ops, kw_full) are shared by every view.
_VIEWS: dict[str, dict[str, Any]] = {
    "gaps": {
        "sort": lambda r: r["kw_missing"],
        "extra_columns": ["kw_missing", "errors", "unsat", "gaps"],
        "formatters": {},
    },
    "rss": {
        "sort": lambda r: r["max_rss_jump_bytes"] or 0,
        "extra_columns": ["rss_gb", "errors"],
        "formatters": {
            "rss_gb": lambda r: (
                "-" if r["max_rss_jump_bytes"] is None else f"{r['max_rss_jump_bytes'] / 1_073_741_824:.2f}"
            )
        },
    },
    "time": {
        "sort": lambda r: r["duration_seconds"],
        "extra_columns": ["duration_s", "errors"],
        "formatters": {"duration_s": lambda r: f"{r['duration_seconds']:.1f}"},
    },
}


def _print_table(rows: list[dict], by: str) -> None:
    if not rows:
        print("no coverage files found", file=sys.stderr)
        return
    view = _VIEWS[by]
    rows.sort(key=view["sort"], reverse=True)
    cols = ["spec", "ops", "kw_full", *view["extra_columns"]]

    def value(row: dict, col: str) -> str:
        formatter = view["formatters"].get(col)
        if formatter is not None:
            return formatter(row)
        return str(row[col])

    widths = {c: max(len(c), max(len(value(r, c)) for r in rows)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    print("  ".join("-" * widths[c] for c in cols))
    for row in rows:
        print("  ".join(value(row, c).ljust(widths[c]) for c in cols))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", type=Path, help="coverage JSON file or directory containing them")
    parser.add_argument(
        "--by",
        choices=tuple(_VIEWS),
        default="gaps",
        help="ranking dimension: 'gaps' (default), 'rss', or 'time'.",
    )
    args = parser.parse_args()

    rows: list[dict] = []
    for path in _iter_files(args.target):
        try:
            data = json.loads(path.read_text())
        except Exception as exc:
            print(f"skip {path.name}: {exc.__class__.__name__}: {exc}", file=sys.stderr)
            continue
        rows.append(_row(path, data))
    _print_table(rows, by=args.by)


if __name__ == "__main__":
    main()
