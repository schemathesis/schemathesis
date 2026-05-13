from __future__ import annotations

import base64
import binascii
import enum
import heapq
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


class CallBucket(enum.Enum):
    POSITIVE_ACCEPTED = "positive_accepted"
    NEGATIVE_REJECTED = "negative_rejected"
    POSITIVE_DRIFT = "positive_drift"
    NEGATIVE_DRIFT = "negative_drift"
    # SUT crashes on valid input — real bug.
    POSITIVE_SERVER_ERROR = "positive_server_error"
    # SUT crashes on invalid input instead of returning 4xx — server still has a bug but
    # the engine did its job; counted separately so "we sent invalid data" doesn't drown
    # out "we sent valid data and the server fell over".
    NEGATIVE_SERVER_ERROR = "negative_server_error"
    ROUTE_REJECTED = "route_rejected"
    AUTH_REJECTED = "auth_rejected"
    OTHER = "other"


class MutationOutcome(enum.Enum):
    REJECTED = "rejected"
    # 2xx — wire-side negation: SUT tolerated the malformed payload.
    ACCEPTED = "accepted"
    # 5xx, 3xx, transport — bumps `count` but leaves rejected/accepted at 0.
    OTHER = "other"


def _classify_mutation_outcome(status: object) -> MutationOutcome:
    if isinstance(status, int):
        if 200 <= status < 300:
            return MutationOutcome.ACCEPTED
        if 400 <= status < 500:
            return MutationOutcome.REJECTED
    return MutationOutcome.OTHER


@dataclass(slots=True)
class Bucket:
    positive_accepted: int = 0
    negative_rejected: int = 0
    positive_drift: int = 0
    negative_drift: int = 0
    positive_server_error: int = 0
    negative_server_error: int = 0
    route_rejected: int = 0
    auth_rejected: int = 0
    other: int = 0

    @property
    def server_error(self) -> int:
        return self.positive_server_error + self.negative_server_error

    @property
    def total(self) -> int:
        return (
            self.positive_accepted
            + self.negative_rejected
            + self.positive_drift
            + self.negative_drift
            + self.positive_server_error
            + self.negative_server_error
            + self.route_rejected
            + self.auth_rejected
            + self.other
        )

    @property
    def handler_reached(self) -> int:
        return (
            self.positive_accepted
            + self.negative_rejected
            + self.positive_drift
            + self.negative_drift
            + self.positive_server_error
            + self.negative_server_error
        )

    @property
    def handler_reached_ratio(self) -> float:
        total = self.total
        return self.handler_reached / total if total else 0.0

    @property
    def drift(self) -> int:
        return self.positive_drift + self.negative_drift

    @property
    def useful(self) -> int:
        return self.positive_accepted + self.negative_rejected + self.positive_server_error + self.negative_server_error

    @property
    def useful_ratio(self) -> float:
        total = self.total
        return self.useful / total if total else 0.0

    def bump(self, kind: CallBucket) -> None:
        match kind:
            case CallBucket.POSITIVE_ACCEPTED:
                self.positive_accepted += 1
            case CallBucket.NEGATIVE_REJECTED:
                self.negative_rejected += 1
            case CallBucket.POSITIVE_DRIFT:
                self.positive_drift += 1
            case CallBucket.NEGATIVE_DRIFT:
                self.negative_drift += 1
            case CallBucket.POSITIVE_SERVER_ERROR:
                self.positive_server_error += 1
            case CallBucket.NEGATIVE_SERVER_ERROR:
                self.negative_server_error += 1
            case CallBucket.ROUTE_REJECTED:
                self.route_rejected += 1
            case CallBucket.AUTH_REJECTED:
                self.auth_rejected += 1
            case CallBucket.OTHER:
                self.other += 1


@dataclass(frozen=True, slots=True)
class FailureRef:
    check_name: str
    operation_label: str
    failure_type: str
    # Excluded from `fingerprint` so message variants collapse into one bucket; retained
    # here so a manual auditor can label "real bug" vs "false positive" without re-running.
    message: str
    # Extracted exception class from the 5xx response body (e.g. `org.hibernate.exception.SQLGrammarException`),
    # used to make distinct-bug counts meaningful instead of timestamp-inflated. Empty
    # string for non-5xx failures or when no class could be recovered.
    exception_signature: str = ""

    @property
    def fingerprint(self) -> str:
        parts = [self.check_name, self.operation_label, self.failure_type]
        if self.exception_signature:
            parts.append(self.exception_signature)
        return "|".join(parts)


# Common Java exception-class patterns leaking into 5xx response bodies. Order matters:
# Spring's `"exception":"..."` JSON field is the cleanest signal when the SUT exposes it;
# otherwise we fall back to FQCN patterns in stack-trace text. `java.lang.*` is last because
# generic exceptions (NullPointerException, IllegalArgumentException) are usually wrapped by
# the framework's exception class which is the more informative root cause.
_EXCEPTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'"exception"\s*:\s*"([^"]+)"'),
    re.compile(r'"type"\s*:\s*"((?:org|com|net)\.[a-zA-Z_][\w.$]*Exception[\w.$]*)"'),
    re.compile(r"(org\.hibernate\.exception\.[A-Z]\w+Exception)"),
    re.compile(r"(org\.springframework\.[a-zA-Z_][\w.]*Exception)"),
    re.compile(r"(com\.mongodb\.[A-Z]\w+Exception)"),
    re.compile(r"(com\.fasterxml\.jackson\.[a-zA-Z_][\w.]*Exception)"),
    re.compile(r"(org\.postgresql\.util\.PSQLException)"),
    re.compile(r"(java\.lang\.[A-Z]\w+Error)"),
    re.compile(r"(java\.lang\.[A-Z]\w+Exception)"),
)


def _extract_exception_signature(body: object) -> str:
    if not isinstance(body, str) or not body:
        return ""
    snippet = body[:4000]
    for pattern in _EXCEPTION_PATTERNS:
        match = pattern.search(snippet)
        if match:
            return match.group(1)
    return ""


@dataclass(slots=True)
class MutationCell:
    count: int = 0
    rejected: int = 0
    accepted: int = 0


@dataclass(slots=True)
class MutationStats:
    by_operator: dict[str, int] = field(default_factory=dict)
    by_location: dict[str, int] = field(default_factory=dict)
    # Keyed by f"{location}|{operator}" for direct JSON serialization (tuples don't serialize).
    grid: dict[str, MutationCell] = field(default_factory=dict)


@dataclass(slots=True)
class Reachability:
    # Numerator only — computing the ratio needs the spec, which isn't in NDJSON.
    covered_operations: list[str] = field(default_factory=list)
    # Operations that received >= BROKEN_OPERATION_MIN_CALLS but never produced a 2xx.
    # A direct surface-area metric: each entry is a path the fuzzer never reached.
    broken_operations: list[str] = field(default_factory=list)


BROKEN_OPERATION_MIN_CALLS = 100


@dataclass(slots=True)
class RateMetrics:
    failures_per_minute: float = 0.0
    twoxx_per_minute: float = 0.0
    # Each row: {"minute": int, "covered": cumulative distinct operations with >= 1 2xx by that minute}.
    new_operation_per_minute_timeline: list[dict[str, int]] = field(default_factory=list)


@dataclass(slots=True)
class CoverageScenarioStats:
    # Reuses MutationCell because the accept-vs-reject semantics are identical.
    by_kind: dict[str, MutationCell] = field(default_factory=dict)


@dataclass(slots=True)
class OperationMetrics:
    label: str
    buckets: Bucket = field(default_factory=Bucket)
    wasted_by_location: dict[str, int] = field(default_factory=dict)
    failures: list[FailureRef] = field(default_factory=list)
    generation_seconds: float = 0.0
    response_seconds: float = 0.0
    # Slowest single-case generation time for this operation, in seconds. Surfaces
    # outliers that the per-operation sum hides — e.g. one 2-second case among hundreds
    # of millisecond-fast ones.
    max_generation_seconds: float = 0.0


@dataclass(slots=True, frozen=True)
class SlowGeneration:
    phase: str
    operation_label: str
    generation_seconds: float
    mode: str


SLOW_GENERATION_TOP_N = 20


@dataclass(slots=True)
class EngineError:
    # `type` is the exception class name from the recorder payload (`KeyError`, `ReadTimeout`, ...).
    # SUT-side noise (`ReadTimeout`, `ConnectionError`) and engine-side bugs (`KeyError`,
    # `Unsatisfiable`, `ValidationError`) live in the same bucket; the reader distinguishes them.
    type: str
    phase: str | None
    operation_label: str | None
    message: str
    count: int


@dataclass(slots=True)
class PhaseMetrics:
    name: str
    duration_seconds: float
    buckets: Bucket = field(default_factory=Bucket)
    truncated: bool = False
    # Cases without timing contribute 0; the denominator (buckets.total) still includes them.
    generation_seconds: float = 0.0
    response_seconds: float = 0.0


@dataclass(slots=True)
class TransitionRecord:
    # Raw `transition.id` from the recorder, e.g.
    # "GET /api/albums -> [200] DeleteAlbum -> DELETE /api/albums/{id}".
    id: str
    # Parsed pieces from `_parse_transition_id`; empty strings when parsing fails.
    source_operation: str
    target_operation: str
    link_name: str
    # Status code like "200" / "201"; empty string when the id is not parseable.
    status_code: str
    is_inferred: bool
    # Times the engine drew this transition.
    count: int = 0
    # Subset of `count` where `is_transition_applied` is true (data extraction succeeded
    # AND the transition's parameters/body were threaded into the new case). The gap
    # between count and applied is the "broken-link" signal.
    applied_count: int = 0
    twoxx: int = 0
    fourxx: int = 0
    fivexx: int = 0
    # 3xx, transport errors, or unknown status.
    other_status: int = 0
    # Depth-from-root statistics. Sum + max let the renderer compute averages
    # without re-iterating cases.
    depth_sum: int = 0
    depth_max: int = 0


@dataclass(slots=True)
class DepthStats:
    # Number of stateful cases the depth was computed for.
    cases: int = 0
    sum: int = 0
    max: int = 0
    # Bucket histogram: depth -> count.
    by_depth: dict[int, int] = field(default_factory=dict)


@dataclass(slots=True)
class TransitionStats:
    by_id: dict[str, TransitionRecord] = field(default_factory=dict)
    depth: DepthStats = field(default_factory=DepthStats)
    # Distinct target operations reached via any transition. Numerator for "engine
    # explored N target ops via stateful"; the spec-aware consumer computes the ratio.
    distinct_targets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PoolEdgeStats:
    """Per-edge attribution: how many times `consumer_operation` drew from `source_operation` for `resource_name`.

    `positive`/`negative` split the count by `generation.mode` so the report can tell apart
    "engine successfully chained the producer" from "engine torture-tested with a real id".
    """

    consumer_operation: str
    source_operation: str
    resource_name: str
    count: int = 0
    positive: int = 0
    negative: int = 0
    twoxx: int = 0
    fourxx: int = 0
    fivexx: int = 0
    # 3xx, transport errors, or unknown status.
    other_status: int = 0


@dataclass(slots=True)
class PoolInventory:
    """Schema-level descriptor inventory carried in `ApiStatistic.resource_pool`.

    The denominator for "engine exercised M of N producers / N consumers".
    Labels are explicit so runtime synthesised operations don't inflate coverage.
    """

    producer_labels: list[str] = field(default_factory=list)
    consumer_labels: list[str] = field(default_factory=list)
    resources: int = 0


@dataclass(slots=True)
class PoolDrawStats:
    # Schema-level inventory captured from the LoadingFinished event.
    inventory: PoolInventory = field(default_factory=PoolInventory)
    # Cases that recorded at least one pool draw.
    cases_with_draws: int = 0
    # Sum of pool_draws across all cases.
    total_draws: int = 0
    # Sum of pool_misses across all cases — slots the engine wanted to fill from the pool
    # but found empty. (draws / (draws + misses)) is the "chain rate".
    total_misses: int = 0
    # Cases that recorded at least one pool miss.
    cases_with_misses: int = 0
    # Edge stats keyed by `f"{consumer}||{source}||{resource}"` so the struct round-trips
    # through JSON; the same identifying tuple lives on each `PoolEdgeStats`.
    by_edge: dict[str, PoolEdgeStats] = field(default_factory=dict)
    by_consumer: dict[str, int] = field(default_factory=dict)
    by_source: dict[str, int] = field(default_factory=dict)
    by_resource: dict[str, int] = field(default_factory=dict)
    # Per-consumer miss rollup: which operations most often wanted a pool value and found none.
    misses_by_consumer: dict[str, int] = field(default_factory=dict)
    # Per-operation 2xx counter populated for ALL phases (incl. stateful). Producer-draw
    # coverage uses this separately from the per-operation `positive_accepted` bucket to
    # keep a 2xx-only view for pool-source rollups.
    twoxx_by_operation: dict[str, int] = field(default_factory=dict)


def _pool_edge_key(consumer_operation: str, source_operation: str, resource_name: str) -> str:
    return f"{consumer_operation}||{source_operation}||{resource_name}"


@dataclass(slots=True)
class RunMetrics:
    schemathesis_version: str
    seed: int | None
    command: str
    duration_seconds: float
    # Generation mode declared via the Initialize event ("positive" / "negative" / "all").
    # `None` when the event predates the field; report-side callouts gate on it.
    mode: str | None = None
    buckets: Bucket = field(default_factory=Bucket)
    status_histogram: dict[int | str, int] = field(default_factory=dict)
    phases: list[PhaseMetrics] = field(default_factory=list)
    operations: dict[str, OperationMetrics] = field(default_factory=dict)
    failures: list[FailureRef] = field(default_factory=list)
    # Raw occurrence count, not deduped — complements `failures` for the
    # "N classes, M occurrences" view.
    failure_counts: dict[str, int] = field(default_factory=dict)
    # Stateful phase emits a synthetic label ("Stateful tests") rather than a real operation;
    # aggregated separately so per-operation rankings stay focused on real operations.
    stateful: OperationMetrics | None = None
    mutations: MutationStats = field(default_factory=MutationStats)
    coverage_scenarios: CoverageScenarioStats = field(default_factory=CoverageScenarioStats)
    rates: RateMetrics = field(default_factory=RateMetrics)
    reachability: Reachability = field(default_factory=Reachability)
    transitions: TransitionStats = field(default_factory=TransitionStats)
    pool_draws: PoolDrawStats = field(default_factory=PoolDrawStats)
    # Top-N slowest single-case generations across the run, sorted descending by
    # `generation_seconds`. Catches corpus / coverage-phase outliers that don't
    # surface in per-operation sums.
    slow_generations: list[SlowGeneration] = field(default_factory=list)
    # `NonFatalError` / `InternalError` events deduped by `(type, phase, operation_label)`,
    # sorted by count desc.
    engine_errors: list[EngineError] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class CallClassification:
    bucket: CallBucket
    # Only populated for POSITIVE_DRIFT.
    locations_present: tuple[str, ...] = ()


def classify_call(call: dict) -> CallClassification:
    status = call["status"]
    mode = (call.get("overall_mode") or "").lower()
    components = call.get("components") or {}
    matches_route = call.get("matches_route", True)

    if status == "transport-error":
        return CallClassification(CallBucket.OTHER)
    if isinstance(status, int):
        if status in (401, 403):
            return CallClassification(CallBucket.AUTH_REJECTED)
        # 404/405 + method mismatch == server confirms the route/method isn't accepted
        # (e.g. coverage method-mutation hitting a path that doesn't allow the mutated method).
        if status in (404, 405) and not matches_route:
            return CallClassification(CallBucket.ROUTE_REJECTED)
        if 500 <= status < 600:
            if mode == "negative":
                return CallClassification(CallBucket.NEGATIVE_SERVER_ERROR)
            return CallClassification(CallBucket.POSITIVE_SERVER_ERROR)
        if 200 <= status < 300:
            if mode == "negative":
                return CallClassification(CallBucket.NEGATIVE_DRIFT)
            return CallClassification(CallBucket.POSITIVE_ACCEPTED)
        if 400 <= status < 500:
            if mode == "negative":
                return CallClassification(CallBucket.NEGATIVE_REJECTED)
            locations_present = tuple(sorted(location for location in components if location != "UNKNOWN"))
            return CallClassification(CallBucket.POSITIVE_DRIFT, locations_present)
    return CallClassification(CallBucket.OTHER)


SCENARIO_FINISHED_EVENTS = {"ScenarioFinished", "FuzzScenarioFinished"}
# Phases that never issue test calls — excluded from per-phase metrics.
SKIP_PHASES = {"API probing", "Schema analysis"}
# Prefix match so future "Stateful X" / "Fuzz X" variants stay grouped.
STATEFUL_LABEL_PREFIX = "Stateful"
FUZZ_LABEL_PREFIX = "Fuzz"


def _is_stateful_label(label: str) -> bool:
    return label.startswith(STATEFUL_LABEL_PREFIX)


def _is_fuzz_label(label: str) -> bool:
    return label.startswith(FUZZ_LABEL_PREFIX)


def _is_synthetic_label(label: str) -> bool:
    return _is_stateful_label(label) or _is_fuzz_label(label)


STATEFUL_LABEL_DEFAULT = "Stateful tests"


def _operation_target(run: RunMetrics, label: str) -> OperationMetrics:
    return run.operations.setdefault(label, OperationMetrics(label=label))


def _stateful_target(run: RunMetrics) -> OperationMetrics:
    if run.stateful is None:
        run.stateful = OperationMetrics(label=STATEFUL_LABEL_DEFAULT)
    return run.stateful


def _iter_calls(payload: dict) -> list[dict]:
    recorder = payload.get("recorder") or {}
    recorder_label = recorder.get("label") or ""
    cases = recorder.get("cases") or {}
    interactions = recorder.get("interactions") or {}
    calls: list[dict] = []
    for case_id, interaction in interactions.items():
        if isinstance(interaction, dict):
            response = interaction.get("response")
            raw_call_timestamp = interaction.get("timestamp")
            call_timestamp = float(raw_call_timestamp) if isinstance(raw_call_timestamp, (int, float)) else None
        else:
            response = None
            call_timestamp = None
        if response is None:
            status: int | str = "transport-error"
            elapsed: float | None = None
        else:
            status = response.get("status_code", "transport-error")
            raw_elapsed = response.get("elapsed")
            elapsed = float(raw_elapsed) if isinstance(raw_elapsed, (int, float)) else None
        case_node = cases.get(case_id) or {}
        case_value = case_node.get("value") or {}
        meta = case_value.get("_meta") or case_value.get("meta") or {}
        generation = meta.get("generation") or {}
        overall_mode = generation.get("mode")
        raw_generation_time = generation.get("time")
        generation_time = float(raw_generation_time) if isinstance(raw_generation_time, (int, float)) else None
        components = {loc_name: (info or {}).get("mode") for loc_name, info in (meta.get("components") or {}).items()}
        operation_label = _attribution_label(recorder_label, case_value)
        matches_route = _matches_operation_route(case_value, _declared_label(recorder_label, case_value))
        calls.append(
            {
                "status": status,
                "overall_mode": overall_mode,
                "components": components,
                "matches_route": matches_route,
                "operation_label": operation_label,
                "is_stateful": _is_stateful_label(recorder_label),
                "generation_time": generation_time,
                "elapsed": elapsed,
                "call_timestamp": call_timestamp,
            }
        )
    return calls


def _attribution_label(recorder_label: str, case_value: dict) -> str:
    # Synthetic recorder labels (fuzz, stateful) don't identify the operation; recover the
    # real per-case method+path. Plain recorder labels (Coverage, Examples) already are the
    # declared operation, so use them directly.
    if _is_synthetic_label(recorder_label):
        return _case_operation_label(case_value) or ""
    return recorder_label or _case_operation_label(case_value)


def _declared_label(recorder_label: str, case_value: dict) -> str:
    # The declared operation, not the per-case one — coverage may mutate case.method while
    # the recorder label still reflects the spec method, and route-match must use the spec.
    if recorder_label and not _is_synthetic_label(recorder_label):
        return recorder_label
    return _case_operation_label(case_value)


def _case_operation_label(case_value: dict) -> str:
    method = case_value.get("method") or ""
    path = case_value.get("path") or ""
    if method and path:
        return f"{method} {path}"
    return ""


def _matches_operation_route(case_value: dict, operation_label: str) -> bool:
    # Coverage phase sends cases with mismatched methods (e.g. TRACE on a POST-only operation);
    # those produce a missing served route, which is the classifier's 'route_rejected' signal.
    if not operation_label or " " not in operation_label:
        return True
    expected_method, _, _ = operation_label.partition(" ")
    case_method = (case_value.get("method") or "").upper()
    if not case_method:
        return True
    return case_method == expected_method.upper()


def _phase_name(payload: dict) -> str | None:
    phase = payload.get("phase")
    if isinstance(phase, dict):
        return phase.get("name")
    if isinstance(phase, str):
        return phase
    return None


def _negative_location(components_raw: dict) -> str:
    # Returns "mixed" when more than one component is in negative mode (per-mutation
    # location can't be reliably disambiguated then) and "unknown" when none are.
    negatives = sorted(
        location
        for location, info in (components_raw or {}).items()
        if isinstance(info, dict) and info.get("mode") == "negative"
    )
    if len(negatives) == 1:
        return negatives[0]
    if not negatives:
        return "unknown"
    return "mixed"


def _accumulate_mutations(payload: dict, mutations: MutationStats) -> None:
    recorder = payload.get("recorder") or {}
    cases = recorder.get("cases") or {}
    interactions = recorder.get("interactions") or {}
    for case_id, case_node in cases.items():
        if not isinstance(case_node, dict):
            continue
        case_value = case_node.get("value") or {}
        meta = case_value.get("_meta") or case_value.get("meta") or {}
        phase_data = (meta.get("phase") or {}).get("data") or {}
        mutation_records = phase_data.get("mutations") or ()
        if not mutation_records:
            continue
        location = _negative_location(meta.get("components") or {})
        interaction = interactions.get(case_id) or {}
        response = interaction.get("response") if isinstance(interaction, dict) else None
        status = response.get("status_code") if isinstance(response, dict) else None
        outcome = _classify_mutation_outcome(status)
        for record in mutation_records:
            if not isinstance(record, dict):
                continue
            operator = record.get("operator") or "unknown"
            mutations.by_operator[operator] = mutations.by_operator.get(operator, 0) + 1
            mutations.by_location[location] = mutations.by_location.get(location, 0) + 1
            key = f"{location}|{operator}"
            cell = mutations.grid.setdefault(key, MutationCell())
            cell.count += 1
            if outcome is MutationOutcome.ACCEPTED:
                cell.accepted += 1
            elif outcome is MutationOutcome.REJECTED:
                cell.rejected += 1


_TRANSITION_ID_PATTERN = re.compile(
    r"^(?P<source>[A-Z]+ [^ ]+) -> \[(?P<status>[^]]+)\] (?P<link>[^ ]+) -> (?P<target>[A-Z]+ [^ ]+)$"
)


def _parse_transition_id(transition_id: str) -> tuple[str, str, str, str]:
    """Split the engine's flat transition.id into ``(source_op, target_op, link_name, status_code)``.

    Engine emits ids like ``"GET /api/albums -> [200] DeleteAlbum -> DELETE /api/albums/{id}"``.
    Returns empty strings for parts when the id doesn't match (e.g., GraphQL or future shapes).
    """
    match = _TRANSITION_ID_PATTERN.match(transition_id)
    if match is None:
        return ("", "", "", "")
    return (match.group("source"), match.group("target"), match.group("link"), match.group("status"))


def _walk_depth(case_id: str, parent_map: dict[str, str | None], cache: dict[str, int]) -> int:
    """Depth from root for a stateful case.

    0 = initial step (no parent); each chained transition adds 1. Memoized via ``cache``;
    ``parent_map`` is built per scenario.
    """
    if case_id in cache:
        return cache[case_id]
    parent = parent_map.get(case_id)
    if parent is None or parent not in parent_map:
        cache[case_id] = 0
        return 0
    depth = 1 + _walk_depth(parent, parent_map, cache)
    cache[case_id] = depth
    return depth


def _accumulate_transitions(payload: dict, transitions: TransitionStats) -> None:
    """Aggregate per-transition counts + depth-from-root across one ScenarioFinished payload.

    Only stateful-phase scenarios are processed — coverage and fuzzing produce a fresh
    parentless case per draw, so their inclusion would just inflate the depth-0 bucket
    without adding signal.
    """
    recorder = payload.get("recorder") or {}
    if not _is_stateful_label(recorder.get("label") or ""):
        return
    cases = recorder.get("cases") or {}
    interactions = recorder.get("interactions") or {}
    parent_map: dict[str, str | None] = {}
    for case_id, case_node in cases.items():
        if isinstance(case_node, dict):
            parent_map[case_id] = case_node.get("parent_id")
    depth_cache: dict[str, int] = {}
    targets: set[str] = set()
    for case_id, case_node in cases.items():
        if not isinstance(case_node, dict):
            continue
        depth = _walk_depth(case_id, parent_map, depth_cache)
        transitions.depth.cases += 1
        transitions.depth.sum += depth
        if depth > transitions.depth.max:
            transitions.depth.max = depth
        transitions.depth.by_depth[depth] = transitions.depth.by_depth.get(depth, 0) + 1
        transition = case_node.get("transition")
        if not isinstance(transition, dict):
            continue
        transition_id = transition.get("id")
        if not isinstance(transition_id, str) or not transition_id:
            continue
        record = transitions.by_id.get(transition_id)
        if record is None:
            source, target, link, status_code = _parse_transition_id(transition_id)
            record = TransitionRecord(
                id=transition_id,
                source_operation=source,
                target_operation=target,
                link_name=link,
                status_code=status_code,
                is_inferred=bool(transition.get("is_inferred")),
            )
            transitions.by_id[transition_id] = record
        record.count += 1
        if case_node.get("is_transition_applied"):
            record.applied_count += 1
        record.depth_sum += depth
        if depth > record.depth_max:
            record.depth_max = depth
        if record.target_operation:
            targets.add(record.target_operation)
        interaction = interactions.get(case_id) or {}
        response = interaction.get("response") if isinstance(interaction, dict) else None
        status = response.get("status_code") if isinstance(response, dict) else None
        if isinstance(status, int):
            if 200 <= status < 300:
                record.twoxx += 1
            elif 400 <= status < 500:
                record.fourxx += 1
            elif 500 <= status < 600:
                record.fivexx += 1
            else:
                record.other_status += 1
        else:
            record.other_status += 1
    if targets:
        existing = set(transitions.distinct_targets)
        existing.update(targets)
        transitions.distinct_targets = sorted(existing)


def _accumulate_pool_draws(payload: dict, stats: PoolDrawStats) -> None:
    """Aggregate per-(consumer, source, resource) pool draws across one ScenarioFinished.

    Each case's ``meta.pool_draws`` lists the captured-resource provenance the engine
    consumed when generating the case. We tally per-edge counts plus 2xx/4xx/5xx outcomes
    from the matching interaction, plus rollups per consumer / producer / resource.
    """
    recorder = payload.get("recorder") or {}
    cases = recorder.get("cases") or {}
    interactions = recorder.get("interactions") or {}
    for case_id, case_node in cases.items():
        if not isinstance(case_node, dict):
            continue
        case_value = case_node.get("value") or {}
        if not isinstance(case_value, dict):
            continue
        method = case_value.get("method")
        path = case_value.get("path")
        if isinstance(method, str) and isinstance(path, str):
            operation_label = f"{method} {path}"
            # Phase-agnostic 2xx counter — covers stateful + non-stateful by walking
            # interactions directly rather than through the per-operation bucket dispatch.
            interaction_for_op = interactions.get(case_id)
            if isinstance(interaction_for_op, dict):
                response_for_op = interaction_for_op.get("response")
                if isinstance(response_for_op, dict):
                    status_for_op = response_for_op.get("status_code")
                    if isinstance(status_for_op, int) and 200 <= status_for_op < 300:
                        stats.twoxx_by_operation[operation_label] = stats.twoxx_by_operation.get(operation_label, 0) + 1
        meta = case_value.get("meta") or case_value.get("_meta") or {}
        if not isinstance(meta, dict):
            continue
        pool_draws = meta.get("pool_draws") or []
        pool_misses = meta.get("pool_misses") or []
        if not pool_draws and not pool_misses:
            continue
        if not isinstance(method, str) or not isinstance(path, str):
            continue
        consumer_operation = f"{method} {path}"
        if pool_misses:
            stats.cases_with_misses += 1
            for miss in pool_misses:
                if not isinstance(miss, list) or len(miss) != 2:
                    continue
                stats.total_misses += 1
                stats.misses_by_consumer[consumer_operation] = stats.misses_by_consumer.get(consumer_operation, 0) + 1
        if not pool_draws:
            continue
        bucket = "other"
        interaction = interactions.get(case_id)
        if isinstance(interaction, dict):
            response = interaction.get("response")
            if isinstance(response, dict):
                status = response.get("status_code")
                if isinstance(status, int):
                    if 200 <= status < 300:
                        bucket = "twoxx"
                    elif 400 <= status < 500:
                        bucket = "fourxx"
                    elif 500 <= status < 600:
                        bucket = "fivexx"
        # Generation mode from case meta, used to split positive vs negative-mutated draws.
        generation = meta.get("generation")
        is_negative = isinstance(generation, dict) and generation.get("mode") == "negative"
        stats.cases_with_draws += 1
        for draw in pool_draws:
            if not isinstance(draw, dict):
                continue
            source_operation = draw.get("source_operation") or ""
            resource_name = draw.get("resource_name") or ""
            edge_key = _pool_edge_key(consumer_operation, source_operation, resource_name)
            edge = stats.by_edge.get(edge_key)
            if edge is None:
                edge = PoolEdgeStats(
                    consumer_operation=consumer_operation,
                    source_operation=source_operation,
                    resource_name=resource_name,
                )
                stats.by_edge[edge_key] = edge
            edge.count += 1
            if is_negative:
                edge.negative += 1
            else:
                edge.positive += 1
            if bucket == "twoxx":
                edge.twoxx += 1
            elif bucket == "fourxx":
                edge.fourxx += 1
            elif bucket == "fivexx":
                edge.fivexx += 1
            else:
                edge.other_status += 1
            stats.total_draws += 1
            stats.by_consumer[consumer_operation] = stats.by_consumer.get(consumer_operation, 0) + 1
            stats.by_source[source_operation] = stats.by_source.get(source_operation, 0) + 1
            stats.by_resource[resource_name] = stats.by_resource.get(resource_name, 0) + 1


def _accumulate_coverage_scenarios(payload: dict, scenarios: CoverageScenarioStats) -> None:
    recorder = payload.get("recorder") or {}
    cases = recorder.get("cases") or {}
    interactions = recorder.get("interactions") or {}
    for case_id, case_node in cases.items():
        if not isinstance(case_node, dict):
            continue
        case_value = case_node.get("value") or {}
        meta = case_value.get("_meta") or case_value.get("meta") or {}
        phase = meta.get("phase") or {}
        if phase.get("name") != "coverage":
            continue
        scenario = (phase.get("data") or {}).get("scenario")
        if not scenario:
            continue
        interaction = interactions.get(case_id) or {}
        response = interaction.get("response") if isinstance(interaction, dict) else None
        status = response.get("status_code") if isinstance(response, dict) else None
        outcome = _classify_mutation_outcome(status)
        cell = scenarios.by_kind.setdefault(scenario, MutationCell())
        cell.count += 1
        if outcome is MutationOutcome.ACCEPTED:
            cell.accepted += 1
        elif outcome is MutationOutcome.REJECTED:
            cell.rejected += 1


def _iter_failures(payload: dict) -> list[FailureRef]:
    recorder = payload.get("recorder") or {}
    recorder_label = recorder.get("label") or ""
    cases = recorder.get("cases") or {}
    interactions = recorder.get("interactions") or {}
    out: list[FailureRef] = []
    for case_id, case_checks in (recorder.get("checks") or {}).items():
        if not isinstance(case_checks, list):
            continue
        case_value = (cases.get(case_id) or {}).get("value") or {}
        label = _attribution_label(recorder_label, case_value)
        response = (interactions.get(case_id) or {}).get("response") or {}
        response_status = response.get("status_code")
        is_5xx_response = isinstance(response_status, int) and 500 <= response_status < 600
        # The response body is reused per case across all of that case's checks; decode once.
        decoded_body: str | None = None
        decode_attempted = False
        for check in case_checks:
            if not isinstance(check, dict) or check.get("status") != "failure":
                continue
            failure = (check.get("failure_info") or {}).get("failure") or {}
            ftype = failure.get("type")
            if not ftype:
                continue
            exception_signature = ""
            # Only extract for actual 5xx responses. ServerError fires on 5xx by definition;
            # UndefinedStatusCode covers any undocumented status, so we must check explicitly
            # before treating its body as a server-side exception payload.
            if ftype in {"ServerError", "UndefinedStatusCode"} and is_5xx_response:
                if not decode_attempted:
                    decode_attempted = True
                    content = response.get("content")
                    if isinstance(content, dict) and "$base64" in content:
                        try:
                            decoded_body = base64.b64decode(content["$base64"]).decode("utf-8", errors="replace")
                        except (ValueError, binascii.Error):
                            decoded_body = None
                    elif isinstance(content, str):
                        decoded_body = content
                if decoded_body:
                    exception_signature = _extract_exception_signature(decoded_body)
            out.append(
                FailureRef(
                    check_name=check.get("name") or "unknown",
                    operation_label=label,
                    failure_type=ftype,
                    message=failure.get("message", "") or "",
                    exception_signature=exception_signature,
                )
            )
    return out


def analyze(path: Path) -> RunMetrics:
    run = RunMetrics(schemathesis_version="", seed=None, command="", duration_seconds=0.0)
    engine_started_at: float | None = None
    last_seen_at: float | None = None
    open_phases: dict[str, dict] = {}
    current_phase: str | None = None
    seen_fingerprints: set[str] = set()
    # Min-heap of (generation_seconds, phase, operation_label, mode) tuples capped at
    # SLOW_GENERATION_TOP_N — cheap top-N over the case stream without retaining every case.
    slow_generation_heap: list[tuple[float, str, str, str]] = []
    # Subset of `covered_operations`: only operations where engine_started_at and a
    # per-call timestamp are both present, which is what the timeline needs.
    first_2xx_minute: dict[str, int] = {}
    covered_operations: set[str] = set()
    twoxx_total = 0
    engine_error_buckets: dict[tuple[str, str | None, str | None], EngineError] = {}
    with open(path, encoding="utf-8") as fd:
        for lineno, line in enumerate(fd, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                sys.stderr.write(f"warning: skipping malformed line {lineno}: {exc}\n")
                continue
            if not isinstance(event, dict) or len(event) != 1:
                continue
            ((event_name, payload),) = event.items()
            timestamp = payload.get("timestamp") if isinstance(payload, dict) else None
            if isinstance(timestamp, (int, float)):
                last_seen_at = float(timestamp)
            if event_name == "Initialize":
                run.schemathesis_version = payload.get("schemathesis_version", "")
                run.seed = payload.get("seed")
                run.command = payload.get("command", "")
                mode = payload.get("mode")
                if isinstance(mode, str):
                    run.mode = mode
            elif event_name == "LoadingFinished":
                statistic = payload.get("statistic")
                if isinstance(statistic, dict):
                    inventory = statistic.get("resource_pool")
                    if isinstance(inventory, dict):
                        producer_labels = inventory.get("producer_labels") or []
                        consumer_labels = inventory.get("consumer_labels") or []
                        if isinstance(producer_labels, list):
                            run.pool_draws.inventory.producer_labels = [
                                label for label in producer_labels if isinstance(label, str)
                            ]
                        if isinstance(consumer_labels, list):
                            run.pool_draws.inventory.consumer_labels = [
                                label for label in consumer_labels if isinstance(label, str)
                            ]
                        run.pool_draws.inventory.resources = int(inventory.get("resources") or 0)
            elif event_name == "EngineStarted" and isinstance(timestamp, (int, float)):
                engine_started_at = float(timestamp)
            elif event_name == "EngineFinished" and isinstance(timestamp, (int, float)):
                if engine_started_at is not None:
                    run.duration_seconds = max(0.0, float(timestamp) - engine_started_at)
            elif event_name in ("NonFatalError", "InternalError"):
                value = payload.get("value")
                if isinstance(value, dict):
                    kind = value.get("type") or "?"
                    message = value.get("message") or ""
                    phase = payload.get("phase")
                    label = payload.get("label")
                    key = (kind, phase, label)
                    existing = engine_error_buckets.get(key)
                    if existing is None:
                        engine_error_buckets[key] = EngineError(
                            type=kind, phase=phase, operation_label=label, message=message, count=1
                        )
                    else:
                        existing.count += 1
            elif event_name == "PhaseStarted":
                name = _phase_name(payload)
                if name and name not in SKIP_PHASES and isinstance(timestamp, (int, float)):
                    open_phases[name] = {
                        "start": float(timestamp),
                        "last_seen": float(timestamp),
                        "buckets": Bucket(),
                        "generation_seconds": 0.0,
                        "response_seconds": 0.0,
                    }
                    current_phase = name
            elif event_name == "PhaseFinished":
                name = _phase_name(payload)
                if name and name in open_phases and isinstance(timestamp, (int, float)):
                    state = open_phases.pop(name)
                    run.phases.append(
                        PhaseMetrics(
                            name=name,
                            duration_seconds=max(0.0, float(timestamp) - state["start"]),
                            buckets=state["buckets"],
                            truncated=False,
                            generation_seconds=state["generation_seconds"],
                            response_seconds=state["response_seconds"],
                        )
                    )
                if current_phase == name:
                    current_phase = None
            elif event_name in SCENARIO_FINISHED_EVENTS:
                for call in _iter_calls(payload):
                    result = classify_call(call)
                    run.buckets.bump(result.bucket)
                    status = call["status"]
                    run.status_histogram[status] = run.status_histogram.get(status, 0) + 1
                    label = call["operation_label"]
                    is_stateful = call.get("is_stateful", False)
                    targets: list[OperationMetrics] = []
                    if label:
                        targets.append(_operation_target(run, label))
                    if is_stateful:
                        targets.append(_stateful_target(run))
                    for operation in targets:
                        operation.buckets.bump(result.bucket)
                        if result.bucket is CallBucket.POSITIVE_DRIFT:
                            for location in result.locations_present:
                                operation.wasted_by_location[location] = (
                                    operation.wasted_by_location.get(location, 0) + 1
                                )
                        if call.get("generation_time") is not None:
                            operation.generation_seconds += call["generation_time"]
                            if call["generation_time"] > operation.max_generation_seconds:
                                operation.max_generation_seconds = call["generation_time"]
                        if call.get("elapsed") is not None:
                            operation.response_seconds += call["elapsed"]
                    # Top-N slow generations are tracked once per call (independent of
                    # dual-bumping into per-op and stateful aggregates above).
                    generation_time = call.get("generation_time")
                    if generation_time is not None:
                        entry = (
                            generation_time,
                            current_phase or "unknown",
                            label or "<unattributed>",
                            call.get("overall_mode") or "unknown",
                        )
                        if len(slow_generation_heap) < SLOW_GENERATION_TOP_N:
                            heapq.heappush(slow_generation_heap, entry)
                        elif generation_time > slow_generation_heap[0][0]:
                            heapq.heappushpop(slow_generation_heap, entry)
                    is_2xx = isinstance(call["status"], int) and 200 <= call["status"] < 300
                    if is_2xx:
                        twoxx_total += 1
                    if is_2xx and label:
                        covered_operations.add(label)
                        # Per-call timestamp (interaction.timestamp) is finer-grained than the
                        # scenario timestamp shared by every case in a ScenarioFinished payload.
                        landing_timestamp = call.get("call_timestamp") or timestamp
                        if (
                            label not in first_2xx_minute
                            and engine_started_at is not None
                            and isinstance(landing_timestamp, (int, float))
                        ):
                            first_2xx_minute[label] = int(max(0.0, float(landing_timestamp) - engine_started_at) // 60)
                    if current_phase and current_phase in open_phases:
                        state = open_phases[current_phase]
                        state["buckets"].bump(result.bucket)
                        if isinstance(timestamp, (int, float)):
                            state["last_seen"] = float(timestamp)
                        if call.get("generation_time") is not None:
                            state["generation_seconds"] += call["generation_time"]
                        if call.get("elapsed") is not None:
                            state["response_seconds"] += call["elapsed"]
                _accumulate_mutations(payload, run.mutations)
                _accumulate_coverage_scenarios(payload, run.coverage_scenarios)
                _accumulate_transitions(payload, run.transitions)
                _accumulate_pool_draws(payload, run.pool_draws)
                for failure in _iter_failures(payload):
                    run.failure_counts[failure.check_name] = run.failure_counts.get(failure.check_name, 0) + 1
                    if failure.fingerprint in seen_fingerprints:
                        continue
                    seen_fingerprints.add(failure.fingerprint)
                    run.failures.append(failure)
                    operation = _operation_target(run, failure.operation_label)
                    if (failure.check_name, failure.fingerprint) not in {
                        (other.check_name, other.fingerprint) for other in operation.failures
                    }:
                        operation.failures.append(failure)
    for name, state in open_phases.items():
        run.phases.append(
            PhaseMetrics(
                name=name,
                duration_seconds=max(0.0, float(state["last_seen"]) - state["start"]),
                buckets=state["buckets"],
                truncated=True,
                generation_seconds=state["generation_seconds"],
                response_seconds=state["response_seconds"],
            )
        )
    if run.duration_seconds == 0.0 and engine_started_at is not None and last_seen_at is not None:
        run.duration_seconds = max(0.0, last_seen_at - engine_started_at)
    _finalize_rates(run, first_2xx_minute, twoxx_total)
    run.slow_generations = [
        SlowGeneration(phase=phase, operation_label=label, generation_seconds=seconds, mode=mode)
        for seconds, phase, label, mode in sorted(slow_generation_heap, reverse=True)
    ]
    run.reachability.covered_operations = sorted(covered_operations)
    # "Broken" means no 2xx of any mode after substantial budget. Use the precomputed
    # covered set so an operation that only succeeded with negative-mode (N+2xx,
    # classified as `negative_drift`) doesn't get mislabelled as broken.
    run.reachability.broken_operations = sorted(
        operation.label
        for operation in run.operations.values()
        if operation.label not in covered_operations and operation.buckets.total >= BROKEN_OPERATION_MIN_CALLS
    )
    run.engine_errors = sorted(
        engine_error_buckets.values(),
        key=lambda entry: (-entry.count, entry.type, entry.phase or "", entry.operation_label or ""),
    )
    return run


def _finalize_rates(run: RunMetrics, first_2xx_minute: dict[str, int], twoxx_total: int) -> None:
    minutes = run.duration_seconds / 60.0
    if minutes > 0:
        run.rates.failures_per_minute = len(run.failures) / minutes
        run.rates.twoxx_per_minute = twoxx_total / minutes
    if not first_2xx_minute:
        return
    last_minute = max(first_2xx_minute.values())
    cumulative = 0
    by_minute = dict.fromkeys(range(last_minute + 1), 0)
    for minute in first_2xx_minute.values():
        by_minute[minute] += 1
    for minute in sorted(by_minute):
        cumulative += by_minute[minute]
        run.rates.new_operation_per_minute_timeline.append({"minute": minute, "covered": cumulative})
