from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from io import StringIO

from .metrics import (
    BROKEN_OPERATION_MIN_CALLS,
    Bucket,
    OperationMetrics,
    PoolDrawStats,
    PoolEdgeStats,
    RunMetrics,
    TransitionRecord,
    TransitionStats,
)


@dataclass(slots=True, frozen=True)
class _TimeRow:
    label: str
    calls: int
    generation: float
    response: float


_HEADLINE_THRESHOLD = 0.25
# Schema-quality finding fires at a lower threshold than the dominant-signal callout,
# because N+2xx (server accepts invalid) is actionable even at single-digit shares.
_SCHEMA_QUALITY_RATIO = 0.05
_SCHEMA_QUALITY_MIN_COUNT = 500


def _budget_rows(buckets: Bucket) -> list[tuple[int, str]]:
    return [
        (buckets.positive_accepted, "Positive accepted (P+2xx)"),
        (buckets.negative_rejected, "Negative rejected (N+4xx)"),
        (buckets.server_error, "Server error (5xx)"),
        (buckets.positive_drift, "Drift: handler rejects valid (P+4xx)"),
        (buckets.negative_drift, "Drift: handler accepts invalid (N+2xx)"),
        (buckets.route_rejected, "Route-rejected"),
        (buckets.auth_rejected, "Auth-rejected"),
        (buckets.other, "Other (3xx, transport, unknown mode)"),
    ]


def _headline_candidates(buckets: Bucket) -> list[tuple[int, str, str]]:
    return [
        (
            buckets.positive_drift,
            "schema/data drift (P+4xx)",
            "random values miss real resources or schema is overly strict",
        ),
        (buckets.server_error, "server errors (5xx)", "server bugs or instability"),
        (buckets.auth_rejected, "auth-rejected", "credentials may be misconfigured"),
        (buckets.route_rejected, "route-rejected", "schema paths/methods do not match the served API"),
        (
            buckets.negative_drift,
            "schema-quality (N+2xx)",
            "server accepts data the schema marks invalid",
        ),
    ]


def _wasted(operation: OperationMetrics) -> int:
    # All four count as budget the run did not learn from: P+4xx and N+2xx never produced
    # an enforceable signal, route/auth never reached the handler.
    buckets = operation.buckets
    return buckets.positive_drift + buckets.negative_drift + buckets.route_rejected + buckets.auth_rejected


def _top_time_rows(run: RunMetrics, limit: int = 10) -> list[_TimeRow]:
    candidates: list[_TimeRow] = []
    for operation in run.operations.values():
        generation = operation.generation_seconds
        response = operation.response_seconds
        if generation + response <= 0:
            continue
        candidates.append(
            _TimeRow(
                label=operation.label,
                calls=operation.buckets.total,
                generation=generation,
                response=response,
            )
        )
    candidates.sort(key=lambda row: -(row.generation + row.response))
    return candidates[:limit]


def _schema_quality_callout(run: RunMetrics) -> str | None:
    total = run.buckets.total
    negative_drift = run.buckets.negative_drift
    if total == 0 or negative_drift == 0:
        return None
    if negative_drift < _SCHEMA_QUALITY_MIN_COUNT and negative_drift / total < _SCHEMA_QUALITY_RATIO:
        return None
    share = negative_drift / total * 100
    return (
        f"**Schema-quality finding:** server accepted {negative_drift} supposedly-invalid "
        f"payloads ({share:.1f}%) — schema may be too narrow."
    )


def _headline(run: RunMetrics) -> str | None:
    total = run.buckets.total
    if total == 0:
        return None
    flagged = [
        (name, count, hint)
        for count, name, hint in _headline_candidates(run.buckets)
        if count / total >= _HEADLINE_THRESHOLD
    ]
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
    rows = _budget_rows(run.buckets)
    width = max(len(label) for _, label in rows)
    write(f"| {'Bucket'.ljust(width)} | Count | Share  |\n")
    write(f"|{'-' * (width + 2)}|-------|--------|\n")
    for count, label in rows:
        share = (count / total * 100) if total else 0.0
        write(f"| {label.ljust(width)} | {count:5d} | {share:5.1f}% |\n")
    write(
        f"\n**Handler-reached: {run.buckets.handler_reached_ratio * 100:.1f}%**"
        f"  ·  **Drift: {run.buckets.drift}** (P+4xx {run.buckets.positive_drift}, "
        f"N+2xx {run.buckets.negative_drift})\n\n"
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
        write("| Operation | Total | Drift+ (P+4xx) | Drift- (N+2xx) | Auth | Route | 5xx | Top location |\n")
        write("|-----------|-------|----------------|----------------|------|-------|-----|--------------|\n")
        for operation in top:
            wasted = operation.wasted_by_location
            top_location = max(wasted, key=lambda location: wasted[location]) if wasted else "-"
            buckets = operation.buckets
            write(
                f"| {operation.label} | {buckets.total} | {buckets.positive_drift} | {buckets.negative_drift} | "
                f"{buckets.auth_rejected} | {buckets.route_rejected} | {buckets.server_error} | {top_location} |\n"
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
        # P+5xx = crash on valid input (real bug); N+5xx = crash on invalid input where
        # the server should have returned 4xx (still a bug, lower severity).
        write("| Operation | Total | 5xx | P+5xx | N+5xx | Ratio |\n")
        write("|-----------|-------|-----|-------|-------|-------|\n")
        for operation in fivexx_operations:
            buckets = operation.buckets
            ratio = (buckets.server_error / buckets.total * 100) if buckets.total else 0.0
            write(
                f"| {operation.label} | {buckets.total} | {buckets.server_error} | "
                f"{buckets.positive_server_error} | {buckets.negative_server_error} | {ratio:.1f}% |\n"
            )
        write("\n")

    timing_rows = _top_time_rows(run)
    if timing_rows:
        write("## Top time-consuming operations\n\n")
        write("Sort key: generation + response (measured per call).\n\n")
        write("| Operation | Calls | Gen | Net | Total |\n")
        write("|-----------|-------|-----|-----|-------|\n")
        for row in timing_rows:
            write(
                f"| {row.label} | {row.calls} | {_format_seconds(row.generation)} | "
                f"{_format_seconds(row.response)} | {_format_seconds(row.generation + row.response)} |\n"
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

    operations_with_traffic = [operation for operation in run.operations.values() if operation.buckets.total > 0]
    if operations_with_traffic:
        total_operations = len(operations_with_traffic)
        # `covered_operations` is the any-mode 2xx set produced by the analyzer pipeline;
        # reached counts include operations that only landed N+2xx (classified as
        # `negative_drift`, not `positive_accepted`). Fall back to bucket data when the
        # pipeline didn't run — test fixtures and partial inputs may skip it — so the
        # rendered counts stay consistent with the rest of the report.
        if run.reachability.covered_operations:
            covered = set(run.reachability.covered_operations)
        else:
            covered = {
                operation.label
                for operation in operations_with_traffic
                if operation.buckets.positive_accepted + operation.buckets.negative_drift > 0
            }
        reached_operations = sum(1 for operation in operations_with_traffic if operation.label in covered)
        broken = run.reachability.broken_operations
        reached_pct = reached_operations * 100 / total_operations if total_operations else 0.0
        # Compact output (no internal blank lines) to keep snapshot diffs free of
        # trailing-whitespace warnings on indent-only lines.
        write("## Reachability\n")
        write(
            f"**Reached:** {reached_operations}/{total_operations} operations "
            f"({reached_pct:.1f}%) produced ≥1 2xx response.\n"
        )
        if broken:
            write(
                f"**Permanently broken:** {len(broken)} operations received "
                f"≥{BROKEN_OPERATION_MIN_CALLS} calls with zero 2xx — "
                f"surfaces the fuzzer never reached.\n"
            )
            for label in broken[:20]:
                operation = run.operations[label]
                write(
                    f"- `{label}` ({operation.buckets.total} calls, "
                    f"{operation.buckets.server_error} × 5xx, "
                    f"{operation.buckets.positive_drift} × P+4xx)\n"
                )
            if len(broken) > 20:
                write(f"- _...and {len(broken) - 20} more_\n")
        # No trailing blank line — next section's `## X\n\n` header provides the gap;
        # emitting an indent-only line here would trip `git diff --check`.

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

    if run.transitions.by_id or run.transitions.depth.cases > 0:
        _render_transitions_section(write, run.transitions)

    # Render whenever the engine *attempted* a pool fill — even runs with 0 draws and only
    # misses (empty pool throughout) are valuable to surface, since that's the case where
    # the chain-rate and "misses by consumer" diagnostics are most actionable.
    if run.pool_draws.total_draws + run.pool_draws.total_misses > 0:
        _render_pool_draws_section(write, run.pool_draws)
    if run.pool_draws.inventory.producer_labels:
        _render_producer_draw_coverage(write, run)

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
            unaccounted_str = _format_duration(unaccounted) if unaccounted >= 0 else "?"
            unaccounted_pct_str = f"{unaccounted_pct:.1f}%" if unaccounted >= 0 else "?"
            write(
                f"| {phase.name} | {wall_str} | {calls} | {_format_seconds(generation)} | "
                f"{_format_per_case_ms(generation / calls) if calls else '-'} | "
                f"{_format_seconds(response)} | "
                f"{_format_per_case_ms(response / calls) if calls else '-'} | "
                f"{unaccounted_str} | {unaccounted_pct_str} |\n"
            )
    else:
        write("_no phases recorded_\n")
    write("\n")

    if run.engine_errors:
        write("## Engine errors\n")
        write(
            "Non-fatal exceptions recorded mid-run: transport noise (`ReadTimeout`, `ConnectionError`) "
            "and engine-side crashes (`KeyError`, `ValidationError`, `Unsatisfiable`).\n"
        )
        write("| Count | Type | Phase | Operation | Message |\n")
        write("|------:|------|-------|-----------|---------|\n")
        for error in run.engine_errors:
            phase_label = error.phase or "-"
            op_label = f"`{error.operation_label}`" if error.operation_label else "-"
            message = error.message.replace("|", "\\|").replace("\n", " ")
            if len(message) > 80:
                message = message[:77] + "..."
            write(f"| {error.count} | `{error.type}` | {phase_label} | {op_label} | {message} |\n")

    if run.slow_generations:
        write("## Slow generation outliers\n")
        write(
            "Top single-case generation times across the run. Useful for catching "
            "corpus / boundary-test code paths that block on long generation rather than I/O.\n"
        )
        write("| Phase | Operation | Mode | Gen time |\n")
        write("|-------|-----------|------|----------|\n")
        for slow in run.slow_generations:
            write(
                f"| {slow.phase} | `{slow.operation_label}` | {slow.mode} | "
                f"{_format_seconds(slow.generation_seconds)} |\n"
            )

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
        # Counted by fingerprint so the headline matches the per-check breakdown when one
        # check emits multiple failure types.
        write(f"{len(run.failures)} unique failures, {total_occurrences} total occurrences:\n")
        for check_name, operations in sorted(by_check.items()):
            operation_count = len(operations)
            unique = unique_per_check[check_name]
            occurrences = run.failure_counts.get(check_name, unique)
            extra = f": {sorted(operations)[0]}" if operation_count == 1 else ""
            suffix = "operation" if operation_count == 1 else "operations"
            write(
                f"- `{check_name}` — {unique} unique, {occurrences} occurrences "
                f"across {operation_count} {suffix}{extra}\n"
            )

        # Bug-count proxy independent of per-response variance (timestamps, ids): the
        # response-body exception class is stable across cases that hit the same bug.
        # Dedupe by (operation, class) so a class flagged by both `ServerError` and
        # `UndefinedStatusCode` on the same operation counts once.
        exception_operations: dict[str, set[str]] = {}
        for failure in run.failures:
            if failure.exception_signature:
                exception_operations.setdefault(failure.exception_signature, set()).add(failure.operation_label)
        if exception_operations:
            write("### Server-side exception classes (5xx response bodies)\n")
            for exception_class, operations in sorted(exception_operations.items(), key=lambda item: -len(item[1])):
                count = len(operations)
                suffix = "s" if count != 1 else ""
                write(f"- `{exception_class}` — {count} distinct (operation, class) pair{suffix}\n")
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
    # Same unit across phases keeps gen/net totals easy to compare at a glance.
    return f"{seconds:.1f}s"


def _format_per_case_ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f}ms"


# Threshold for the "broken-link candidates" callout: transitions firing at least this
# many times AND with applied-rate at most this share. The pair flags an engine wasting
# budget on transitions whose data extraction never threads through.
_BROKEN_LINK_MIN_COUNT = 20
_BROKEN_LINK_MAX_APPLIED_RATE = 0.10
# How many top entries to surface in each pool-draw rollup table.
_POOL_TOP_N = 10


def _render_transitions_section(write: Callable[[str], int], stats: TransitionStats) -> None:
    write("## Stateful transitions\n\n")
    distinct = len(stats.by_id)
    write(f"{distinct} distinct transitions exercised, {len(stats.distinct_targets)} target operations reached.\n")
    cases = stats.depth.cases
    if cases > 0:
        avg_depth = stats.depth.sum / cases
        write(f"Depth from root across {cases} stateful cases: avg {avg_depth:.2f}, max {stats.depth.max}.\n")
        chained = sum(count for depth, count in stats.depth.by_depth.items() if int(depth) > 0)
        chained_pct = chained / cases * 100
        write(f"Chained beyond initial step: {chained}/{cases} ({chained_pct:.1f}%).\n\n")
        write("```\n")
        write("Depth  Cases   Share\n")
        for depth in sorted(stats.depth.by_depth, key=int):
            count = stats.depth.by_depth[depth]
            share = count / cases * 100
            bars = "█" * max(1, int(share / 5))
            write(f"  {int(depth):>2}  {count:>6}  {share:>5.1f}%  {bars}\n")
        write("```\n\n")
    else:
        write("\n")

    if not stats.by_id:
        return

    broken = _broken_link_candidates(stats.by_id)
    if broken:
        write(
            f"### Broken-link candidates  (≥ {_BROKEN_LINK_MIN_COUNT} cases, "
            f"≤ {int(_BROKEN_LINK_MAX_APPLIED_RATE * 100)}% applied)\n\n"
        )
        write("Engine drew the transition repeatedly but data extraction never threaded ")
        write("through — prime targets for dependency-pruning.\n\n")
        write("| Transition | Cases | Applied% | 2xx | 4xx | 5xx |\n")
        write("|------------|------:|---------:|----:|----:|----:|\n")
        for record in broken:
            applied_pct = record.applied_count / record.count * 100 if record.count else 0.0
            write(
                f"| {record.id} | {record.count} | {applied_pct:.1f}% | "
                f"{record.twoxx} | {record.fourxx} | {record.fivexx} |\n"
            )
        write("\n")

    top_count = sorted(stats.by_id.values(), key=lambda r: -r.count)[:10]
    write("### Top transitions by case count\n\n")
    write("| Transition | Cases | Applied% | 2xx | 4xx | 5xx | Avg depth | Inferred |\n")
    write("|------------|------:|---------:|----:|----:|----:|----------:|----------|\n")
    for record in top_count:
        applied_pct = record.applied_count / record.count * 100 if record.count else 0.0
        avg_depth = record.depth_sum / record.count if record.count else 0.0
        write(
            f"| {record.id} | {record.count} | {applied_pct:.1f}% | "
            f"{record.twoxx} | {record.fourxx} | {record.fivexx} | "
            f"{avg_depth:.1f} | {'yes' if record.is_inferred else 'no'} |\n"
        )
    write("\n")

    fivexx_rows = [r for r in stats.by_id.values() if r.fivexx > 0]
    fivexx_rows.sort(key=lambda r: -r.fivexx)
    if fivexx_rows:
        write("### Top transitions by server-error rate  (bug-discovery hot paths)\n\n")
        write("| Transition | Cases | 5xx | 5xx% |\n")
        write("|------------|------:|----:|-----:|\n")
        for record in fivexx_rows[:10]:
            rate = record.fivexx / record.count * 100 if record.count else 0.0
            write(f"| {record.id} | {record.count} | {record.fivexx} | {rate:.1f}% |\n")
        write("\n")


def _broken_link_candidates(by_id: dict[str, TransitionRecord]) -> list[TransitionRecord]:
    candidates: list[TransitionRecord] = []
    for record in by_id.values():
        if record.count < _BROKEN_LINK_MIN_COUNT:
            continue
        applied_rate = record.applied_count / record.count if record.count else 0.0
        if applied_rate <= _BROKEN_LINK_MAX_APPLIED_RATE:
            candidates.append(record)
    candidates.sort(key=lambda r: -r.count)
    return candidates[:10]


def _render_pool_draws_section(write: Callable[[str], int], stats: PoolDrawStats) -> None:
    write("## Resource pool draws\n\n")
    edges = list(stats.by_edge.values())
    inv = stats.inventory
    if inv.producer_labels or inv.consumer_labels:
        write(
            f"Schema inventory: {len(inv.producer_labels)} producer operations, "
            f"{len(inv.consumer_labels)} consumer operations, {inv.resources} distinct resource types.\n"
        )
        producer_set = set(inv.producer_labels)
        consumer_set = set(inv.consumer_labels)
        producers_hit = {edge.source_operation for edge in edges if edge.source_operation in producer_set}
        consumers_hit = {edge.consumer_operation for edge in edges if edge.consumer_operation in consumer_set}
        if producer_set:
            write(
                f"Producer coverage: {len(producers_hit)}/{len(producer_set)} declared producers supplied "
                "at least one draw.\n"
            )
        if consumer_set:
            write(
                f"Consumer coverage: {len(consumers_hit)}/{len(consumer_set)} declared consumers made "
                "at least one draw.\n"
            )
        write("\n")
    write(
        f"{stats.cases_with_draws} cases consumed pool data ({stats.total_draws} draws total) "
        f"across {len(edges)} unique consumer-producer edges.\n"
    )
    attempted = stats.total_draws + stats.total_misses
    if attempted > 0:
        chain_rate = stats.total_draws / attempted * 100
        write(
            f"Chain rate: {chain_rate:.1f}% — {stats.total_draws} of {attempted} resource-bound slot "
            f"fills found a pool entry; {stats.total_misses} hit an empty pool.\n"
        )
    write("\n")

    if stats.misses_by_consumer:
        write("### Empty-pool misses by consumer\n\n")
        write(
            "Operations the engine wanted to chain into but found no captured value for. High counts "
            "early in the run typically resolve as more producers fire; persistent counts flag "
            "broken or unreachable producers.\n\n"
        )
        write("| Consumer | Misses |\n")
        write("|----------|-------:|\n")
        for label, count in _top_pairs(stats.misses_by_consumer, _POOL_TOP_N):
            write(f"| {label} | {count} |\n")
        write("\n")

    if stats.total_draws == 0:
        # Inventory + chain rate + misses tables above are the full diagnostic for empty runs.
        return

    write("### Top consumers by draw count\n\n")
    write("| Consumer | Draws |\n")
    write("|----------|------:|\n")
    for label, count in _top_pairs(stats.by_consumer, _POOL_TOP_N):
        write(f"| {label} | {count} |\n")
    write("\n")

    write("### Top producers by draw count\n\n")
    write("| Producer | Draws |\n")
    write("|----------|------:|\n")
    for label, count in _top_pairs(stats.by_source, _POOL_TOP_N):
        write(f"| {label} | {count} |\n")
    write("\n")

    write("### Top edges by draw count\n\n")
    write("Edges read producer -> consumer (data-flow direction).\n")
    write("Pos/Neg splits how many draws were on positive vs negative-mode cases — the latter\n")
    write("are real ids torture-tested with mutated bodies/params and tend to land 4xx by design.\n\n")
    write("| Producer | Consumer | Resource | Draws | Pos | Neg | 2xx% | 4xx% | 5xx% |\n")
    write("|----------|----------|----------|------:|----:|----:|-----:|-----:|-----:|\n")
    edges.sort(key=lambda edge: -edge.count)
    for edge in edges[:_POOL_TOP_N]:
        write(_format_edge_row(edge))
    write("\n")

    fivexx_edges = [edge for edge in edges if edge.fivexx > 0]
    if fivexx_edges:
        write("### Edges with the highest 5xx counts  (server-error hot paths)\n\n")
        write("| Producer | Consumer | Resource | Draws | 5xx | 5xx% |\n")
        write("|----------|----------|----------|------:|----:|-----:|\n")
        fivexx_edges.sort(key=lambda edge: -edge.fivexx)
        for edge in fivexx_edges[:_POOL_TOP_N]:
            rate = edge.fivexx / edge.count * 100 if edge.count else 0.0
            write(
                f"| {edge.source_operation} | {edge.consumer_operation} | {edge.resource_name} "
                f"| {edge.count} | {edge.fivexx} | {rate:.1f}% |\n"
            )
        write("\n")


def _top_pairs(counts: dict[str, int], n: int) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda kv: -kv[1])[:n]


def _format_edge_row(edge: PoolEdgeStats) -> str:
    twoxx_rate = edge.twoxx / edge.count * 100 if edge.count else 0.0
    fourxx_rate = edge.fourxx / edge.count * 100 if edge.count else 0.0
    fivexx_rate = edge.fivexx / edge.count * 100 if edge.count else 0.0
    return (
        f"| {edge.source_operation} | {edge.consumer_operation} | {edge.resource_name} "
        f"| {edge.count} | {edge.positive} | {edge.negative} "
        f"| {twoxx_rate:.1f}% | {fourxx_rate:.1f}% | {fivexx_rate:.1f}% |\n"
    )


def _render_producer_draw_coverage(write: Callable[[str], int], run: RunMetrics) -> None:
    """Surface declared producers that supplied no pool draws across the run.

    `by_source` counts draws made by *consumers*, so "0 draws" can mean either the
    extractor never recognised the producer's response OR no selected consumer needed
    that producer's resource. Without an explicit per-producer capture count we can't
    separate the two, so the section is framed as "draw coverage" rather than a verdict.
    """
    producer_labels = run.pool_draws.inventory.producer_labels
    by_source = run.pool_draws.by_source
    twoxx_by_operation = run.pool_draws.twoxx_by_operation
    not_drawn_with_traffic: list[tuple[str, int]] = []
    not_drawn_without_traffic: list[str] = []
    for label in producer_labels:
        if by_source.get(label, 0) > 0:
            continue
        twoxx_count = twoxx_by_operation.get(label, 0)
        if twoxx_count > 0:
            not_drawn_with_traffic.append((label, twoxx_count))
        else:
            not_drawn_without_traffic.append(label)
    if not not_drawn_with_traffic and not not_drawn_without_traffic:
        return
    write("## Producer draw coverage\n\n")
    write(
        "Declared producers that supplied zero pool draws this run. Possible causes: the "
        "response-shape extractor doesn't recognise this producer, no selected consumer "
        "references the resource it captures, or — for the no-2xx group — the producer never "
        "succeeded.\n\n"
    )
    if not_drawn_with_traffic:
        write("Reached the SUT (>= 1 2xx) but no draw was made from its output:\n\n")
        write("| Producer | 2xx calls | Draws supplied |\n")
        write("|----------|----------:|---------------:|\n")
        not_drawn_with_traffic.sort(key=lambda row: -row[1])
        for label, twoxx_count in not_drawn_with_traffic:
            write(f"| {label} | {twoxx_count} | 0 |\n")
        write("\n")
    if not_drawn_without_traffic:
        write(
            f"{len(not_drawn_without_traffic)} declared producer(s) had no 2xx call this run — "
            "auth / setup / dependency-ordering issue first, not a draw-coverage question:\n\n"
        )
        for label in sorted(not_drawn_without_traffic):
            write(f"- `{label}`\n")
        write("\n")
