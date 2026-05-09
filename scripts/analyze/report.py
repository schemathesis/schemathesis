from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from io import StringIO

from .metrics import OperationMetrics, RunMetrics


@dataclass(slots=True, frozen=True)
class _TimeRow:
    label: str
    calls: int
    generation: float
    response: float
    unaccounted: float
    total: float


_BUDGET_ROWS: list[tuple[str, str]] = [
    ("positive_accepted", "Positive accepted (P+2xx)"),
    ("negative_rejected", "Negative rejected (N+4xx)"),
    ("server_error", "Server error (5xx)"),
    ("positive_drift", "Drift: handler rejects valid (P+4xx)"),
    ("negative_drift", "Drift: handler accepts invalid (N+2xx)"),
    ("route_rejected", "Route-rejected"),
    ("auth_rejected", "Auth-rejected"),
    ("other", "Other (3xx, transport, unknown mode)"),
]

# Headline candidates, in preferred order when multiple cross threshold.
_HEADLINE_CANDIDATES: list[tuple[str, str, str]] = [
    ("positive_drift", "schema/data drift (P+4xx)", "random values miss real resources or schema is overly strict"),
    ("server_error", "server errors (5xx)", "server bugs or instability"),
    ("auth_rejected", "auth-rejected", "credentials may be misconfigured"),
    ("route_rejected", "route-rejected", "schema paths/methods do not match the served API"),
    ("negative_drift", "schema-quality (N+2xx)", "server accepts data the schema marks invalid"),
]
_HEADLINE_THRESHOLD = 0.25
# Schema-quality finding fires at a lower threshold than the dominant-signal callout,
# because N+2xx (server accepts invalid) is actionable even at single-digit shares.
_SCHEMA_QUALITY_RATIO = 0.05
_SCHEMA_QUALITY_MIN_COUNT = 500


def _wasted(operation: OperationMetrics) -> int:
    # `negative_drift` is intentionally excluded — it's a schema-quality finding,
    # not wasted budget.
    buckets = operation.buckets
    return buckets.positive_drift + buckets.route_rejected + buckets.auth_rejected


def _top_time_rows(run: RunMetrics, limit: int = 10) -> list[_TimeRow]:
    total_calls = sum(phase.buckets.total for phase in run.phases)
    total_unaccounted = sum(
        max(0.0, phase.duration_seconds - phase.generation_seconds - phase.response_seconds) for phase in run.phases
    )
    if total_calls == 0:
        return []
    candidates: list[_TimeRow] = []
    for operation in run.operations.values():
        generation = operation.generation_seconds
        response = operation.response_seconds
        unaccounted = (operation.buckets.total / total_calls) * total_unaccounted
        operation_total = generation + response + unaccounted
        if operation_total <= 0:
            continue
        candidates.append(
            _TimeRow(
                label=operation.label,
                calls=operation.buckets.total,
                generation=generation,
                response=response,
                unaccounted=unaccounted,
                total=operation_total,
            )
        )
    candidates.sort(key=lambda row: -row.total)
    return candidates[:limit]


def _schema_quality_callout(run: RunMetrics) -> str | None:
    total = run.buckets.total
    nd = run.buckets.negative_drift
    if total == 0 or nd == 0:
        return None
    if nd < _SCHEMA_QUALITY_MIN_COUNT and nd / total < _SCHEMA_QUALITY_RATIO:
        return None
    share = nd / total * 100
    return (
        f"**Schema-quality finding:** server accepted {nd} supposedly-invalid "
        f"payloads ({share:.1f}%) — schema may be too narrow."
    )


def _headline(run: RunMetrics) -> str | None:
    total = run.buckets.total
    if total == 0:
        return None
    flagged = []
    for attr, name, hint in _HEADLINE_CANDIDATES:
        count = getattr(run.buckets, attr)
        if count / total >= _HEADLINE_THRESHOLD:
            flagged.append((name, count, hint))
    if not flagged:
        return None
    flagged.sort(key=lambda item: -item[1])
    if len(flagged) == 1:
        name, count, hint = flagged[0]
        return f"**Dominant signal:** {name} ({count / total * 100:.1f}%) — {hint}"
    parts = [f"{name} ({count / total * 100:.1f}%)" for name, count, _ in flagged]
    return "**Dominant signals:** " + ", ".join(parts)


def render_markdown(run: RunMetrics) -> str:
    out = StringIO()
    write = out.write

    total = run.buckets.total
    write("# Schemathesis run report\n\n")
    write(
        f"v{run.schemathesis_version} · seed {run.seed} · "
        f"duration {_format_duration(run.duration_seconds)} · {total} calls\n\n"
    )

    headline = _headline(run)
    if headline:
        write(headline + "\n\n")

    schema_quality = _schema_quality_callout(run)
    if schema_quality:
        write(schema_quality + "\n\n")

    write("## Budget utilisation\n\n")
    width = max(len(label) for _, label in _BUDGET_ROWS)
    write(f"| {'Bucket'.ljust(width)} | Count | Share  |\n")
    write(f"|{'-' * (width + 2)}|-------|--------|\n")
    for attr, label in _BUDGET_ROWS:
        count = getattr(run.buckets, attr)
        share = (count / total * 100) if total else 0.0
        write(f"| {label.ljust(width)} | {count:5d} | {share:5.1f}% |\n")
    write(
        f"\n**Handler-reached: {run.buckets.handler_reached_ratio * 100:.1f}%**"
        f"  ·  **Drift: {run.buckets.drift}** (P+4xx {run.buckets.positive_drift}, N+2xx {run.buckets.negative_drift})\n\n"
    )

    write("### Drift by location  (P+4xx, locations participating)\n\n")
    overall_locations: dict[str, int] = {}
    for operation in run.operations.values():
        for location, count in operation.wasted_by_location.items():
            overall_locations[location] = overall_locations.get(location, 0) + count
    if overall_locations:
        write("| Location | Calls |\n|----------|-------|\n")
        for location, count in sorted(overall_locations.items(), key=lambda item: -item[1]):
            write(f"| {location:<8} | {count:5d} |\n")
    else:
        write("_no positive-drift calls_\n")
    write("\n")

    write("## Top wasted operations\n\n")
    ranked = sorted(run.operations.values(), key=lambda operation: -_wasted(operation))
    top = [operation for operation in ranked if _wasted(operation) > 0][:10]
    if top:
        write("| Operation | Total | Drift | Auth | Route | 5xx | Top loc |\n")
        write("|-----------|-------|-------|------|-------|-----|---------|\n")
        for operation in top:
            wasted = operation.wasted_by_location
            top_loc = max(wasted, key=lambda location: wasted[location]) if wasted else "-"
            buckets = operation.buckets
            write(
                f"| {operation.label} | {buckets.total} | {buckets.positive_drift} | {buckets.auth_rejected} | "
                f"{buckets.route_rejected} | {buckets.server_error} | {top_loc} |\n"
            )
    else:
        write("_no wasted calls_\n")
    write("\n")

    fivexx_operations = sorted(
        (operation for operation in run.operations.values() if operation.buckets.server_error > 0),
        key=lambda operation: -operation.buckets.server_error,
    )[:10]
    if fivexx_operations:
        write("## Top server-error operations\n\n")
        write("| Operation | Total | 5xx | Ratio |\n")
        write("|-----------|-------|-----|-------|\n")
        for operation in fivexx_operations:
            buckets = operation.buckets
            ratio = (buckets.server_error / buckets.total * 100) if buckets.total else 0.0
            write(f"| {operation.label} | {buckets.total} | {buckets.server_error} | {ratio:.1f}% |\n")
        write("\n")

    timing_rows = _top_time_rows(run)
    if timing_rows:
        write("## Top time-consuming operations\n\n")
        write(
            "Sort key: generation + response + prorated unaccounted. "
            "Unaccounted is allocated per call (operation calls / run calls x run unaccounted) "
            "since the NDJSON has no per-call wall timestamps.\n\n"
        )
        write("| Operation | Calls | Gen | Net | Unaccounted | Total |\n")
        write("|-----------|-------|-----|-----|-------------|-------|\n")
        for row in timing_rows:
            write(
                f"| {row.label} | {row.calls} | {_format_seconds(row.generation)} | "
                f"{_format_seconds(row.response)} | {_format_duration(row.unaccounted)} | "
                f"{_format_duration(row.total)} |\n"
            )
        write("\n")

    never_reached = [
        operation
        for operation in run.operations.values()
        if operation.buckets.total > 0 and operation.buckets.handler_reached_ratio == 0.0
    ]
    if never_reached:
        write("## Operations that never reached the handler\n")
        for operation in never_reached:
            write(f"- `{operation.label}` ({operation.buckets.total} calls, 0% handler-reached)\n")
        write("\n")

    if run.stateful is not None:
        stateful = run.stateful
        buckets = stateful.buckets
        write("## Stateful summary\n\n")
        write(
            f"`{stateful.label}` — {buckets.total} calls, "
            f"handler-reached {buckets.handler_reached_ratio * 100:.1f}%, "
            f"drift {buckets.drift} (P+4xx {buckets.positive_drift}, N+2xx {buckets.negative_drift}), "
            f"5xx {buckets.server_error}\n"
        )
        if stateful.wasted_by_location:
            locations = ", ".join(
                f"{location} {count}"
                for location, count in sorted(stateful.wasted_by_location.items(), key=lambda item: -item[1])
            )
            write(f"Drift by location: {locations}\n")
        write("\n")

    write("## Phases\n\n")
    non_empty = [phase for phase in run.phases if phase.buckets.total > 0]
    if non_empty:
        write("| Phase | Wall | Calls | Gen total | Gen/case | Net total | Net/case | Unaccounted | Unaccounted% |\n")
        write("|-------|------|-------|-----------|----------|-----------|----------|-------------|--------------|\n")
        for phase in non_empty:
            wall = phase.duration_seconds
            calls = phase.buckets.total
            generation = phase.generation_seconds
            response = phase.response_seconds
            unaccounted = wall - generation - response
            unaccounted_pct = (unaccounted / wall * 100) if wall > 0 else 0.0
            wall_str = _format_duration(wall) + ("*" if phase.truncated else "")
            unacc_str = _format_duration(unaccounted) if unaccounted >= 0 else "?"
            unacc_pct_str = f"{unaccounted_pct:.1f}%" if unaccounted >= 0 else "?"
            write(
                f"| {phase.name} | {wall_str} | {calls} | {_format_seconds(generation)} | "
                f"{_format_per_case_ms(generation / calls) if calls else '-'} | "
                f"{_format_seconds(response)} | "
                f"{_format_per_case_ms(response / calls) if calls else '-'} | "
                f"{unacc_str} | {unacc_pct_str} |\n"
            )
    else:
        write("_no phases recorded_\n")
    write("\n")

    write("## Status codes (run-wide)\n\n")
    grouped = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "transport": 0}
    for status, count in run.status_histogram.items():
        if isinstance(status, int):
            band = f"{status // 100}xx"
            if band in grouped:
                grouped[band] += count
        elif status == "transport-error":
            grouped["transport"] += count
    write("  ".join(f"{label}: {count}" for label, count in grouped.items()) + "\n\n")

    write("## Failures\n\n")
    if run.failures:
        by_check: dict[str, set[str]] = {}
        unique_per_check: dict[str, int] = {}
        for failure in run.failures:
            by_check.setdefault(failure.check_name, set()).add(failure.operation_label)
            unique_per_check[failure.check_name] = unique_per_check.get(failure.check_name, 0) + 1
        total_occurrences = sum(run.failure_counts.values())
        # Count by fingerprint (one per distinct (check, op, failure_type) bucket) so the
        # headline matches the per-check breakdown when a single check emits multiple types.
        write(f"{len(run.failures)} unique failures, {total_occurrences} total occurrences:\n")
        for check_name, operations in sorted(by_check.items()):
            operation_count = len(operations)
            unique = unique_per_check[check_name]
            occurrences = run.failure_counts.get(check_name, unique)
            extra = f": {sorted(operations)[0]}" if operation_count == 1 else ""
            write(
                f"- `{check_name}` — {unique} unique, {occurrences} occurrences "
                f"across {operation_count} op{'s' if operation_count != 1 else ''}{extra}\n"
            )
    else:
        write("_no failures_\n")
    return out.getvalue()


def render_json(run: RunMetrics) -> str:
    return json.dumps(asdict(run), indent=2, default=str)


def _format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(seconds, 60)
    return f"{int(minutes)}m {secs:.0f}s"


def _format_seconds(seconds: float) -> str:
    # Render in plain seconds. Used for cumulative gen/net totals where keeping the
    # same unit across phases makes them easy to compare visually.
    return f"{seconds:.1f}s"


def _format_per_case_ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f}ms"
