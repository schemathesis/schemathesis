from __future__ import annotations

from collections import Counter
from typing import Any

from tools.coverage.audit import SchemaResult

TOP_UNCOVERED_PATHS = 30
WORST_APIS = 10


def _bucket_full_partial(statistic: dict[str, Any], key: str) -> tuple[int, int]:
    bucket = statistic.get(key) or {}
    total = int(bucket.get("total", 0))
    covered = int(bucket.get("full", 0)) + int(bucket.get("partial", 0))
    return covered, total


def _bucket_seen(statistic: dict[str, Any], key: str) -> tuple[int, int]:
    bucket = statistic.get(key) or {}
    return int(bucket.get("seen", 0)), int(bucket.get("total", 0))


def _percent(covered: int, total: int) -> float:
    return (covered / total * 100) if total else 0.0


def aggregate(results: list[SchemaResult]) -> dict[str, Any]:
    apis_with_results = apis_errored = 0
    cases_generated = 0
    duration_seconds = 0.0

    operations_seen = operations_total = 0
    keywords_full = keywords_full_partial = keywords_total = 0
    parameters_covered = parameters_total = 0
    examples_seen = examples_total = 0

    gap_kinds: Counter[str] = Counter()
    uncovered_path_counts: Counter[str] = Counter()
    uncovered_states: dict[str, Counter[str]] = {}
    keyword_pcts: list[tuple[float, str, str]] = []

    for result in results:
        if result.errors:
            apis_errored += 1
        statistic = result.statistic
        if not statistic:
            continue
        apis_with_results += 1
        cases_generated += result.cases_generated
        duration_seconds += result.duration_seconds

        seen, total = _bucket_seen(statistic, "operations")
        operations_seen += seen
        operations_total += total

        bucket = statistic.get("keywords") or {}
        keywords_full += int(bucket.get("full", 0))
        covered, total = _bucket_full_partial(statistic, "keywords")
        keywords_full_partial += covered
        keywords_total += total
        if total:
            keyword_pcts.append((covered / total * 100, result.corpus, result.api))

        covered, total = _bucket_full_partial(statistic, "parameters")
        parameters_covered += covered
        parameters_total += total

        seen, total = _bucket_seen(statistic, "examples")
        examples_seen += seen
        examples_total += total

        for gap in result.gaps:
            gap_kinds[gap.get("kind", "unknown")] += 1

        for entry in result.uncovered_keywords:
            schema_path = entry.get("schema_path") or "<missing>"
            uncovered_path_counts[schema_path] += 1
            uncovered_states.setdefault(schema_path, Counter())[entry.get("state") or "unknown"] += 1

    keyword_pcts.sort()
    return {
        "phase": results[0].phase if results else None,
        "apis_total": len(results),
        "apis_with_results": apis_with_results,
        "apis_errored": apis_errored,
        "cases_generated": cases_generated,
        "duration_seconds": round(duration_seconds, 2),
        "rates": {
            "operations": {
                "covered": operations_seen,
                "total": operations_total,
                "pct": _percent(operations_seen, operations_total),
            },
            "keywords_full_only": {
                "covered": keywords_full,
                "total": keywords_total,
                "pct": _percent(keywords_full, keywords_total),
            },
            "keywords_partial_or_full": {
                "covered": keywords_full_partial,
                "total": keywords_total,
                "pct": _percent(keywords_full_partial, keywords_total),
            },
            "parameters_partial_or_full": {
                "covered": parameters_covered,
                "total": parameters_total,
                "pct": _percent(parameters_covered, parameters_total),
            },
            "examples": {
                "covered": examples_seen,
                "total": examples_total,
                "pct": _percent(examples_seen, examples_total),
            },
        },
        "gap_kinds": gap_kinds.most_common(),
        "top_uncovered_paths": [
            {
                "schema_path": path,
                "occurrences": count,
                "states": dict(uncovered_states.get(path, Counter())),
            }
            for path, count in uncovered_path_counts.most_common(TOP_UNCOVERED_PATHS)
        ],
        "worst_apis": [
            {"corpus": corpus, "api": api, "keyword_pct": round(pct, 2)}
            for pct, corpus, api in keyword_pcts[:WORST_APIS]
        ],
    }


def _row(label: str, bucket: dict[str, Any]) -> str:
    return f"| {label} | {bucket['covered']} | {bucket['total']} | {bucket['pct']:.1f}% |"


def render_markdown(summary: dict[str, Any]) -> str:
    phase = summary.get("phase") or "unknown"
    lines: list[str] = [f"# Coverage audit summary ({phase} phase)", ""]
    lines.append(
        f"APIs audited: **{summary['apis_with_results']}** / {summary['apis_total']} "
        f"(errored: {summary['apis_errored']})"
    )
    lines.append(f"Cases generated: **{summary['cases_generated']:,}** in {summary['duration_seconds']:.1f}s")
    lines.append("")

    rates = summary["rates"]
    lines += [
        "## Aggregate coverage",
        "",
        "| metric | covered | total | % |",
        "|---|---:|---:|---:|",
        _row("operations", rates["operations"]),
        _row("keywords (full only)", rates["keywords_full_only"]),
        _row("keywords (partial+full)", rates["keywords_partial_or_full"]),
        _row("parameters (partial+full)", rates["parameters_partial_or_full"]),
        _row("examples", rates["examples"]),
        "",
    ]

    if summary["gap_kinds"]:
        lines += ["## Gap kinds (across all APIs)", "", "| kind | count |", "|---|---:|"]
        lines += [f"| `{kind}` | {count} |" for kind, count in summary["gap_kinds"]]
        lines.append("")

    if summary["top_uncovered_paths"]:
        lines += ["## Most-missed schema paths", "", "| schema_path | occurrences | top states |", "|---|---:|---|"]
        for entry in summary["top_uncovered_paths"]:
            states = ", ".join(f"{state}={count}" for state, count in entry["states"].items())
            lines.append(f"| `{entry['schema_path']}` | {entry['occurrences']} | {states} |")
        lines.append("")

    if summary["worst_apis"]:
        lines += ["## Worst-covered APIs (by keyword %)", "", "| corpus | api | keyword % |", "|---|---|---:|"]
        for entry in summary["worst_apis"]:
            lines.append(f"| {entry['corpus']} | {entry['api']} | {entry['keyword_pct']:.1f}% |")
        lines.append("")

    return "\n".join(lines)
