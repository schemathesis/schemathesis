from __future__ import annotations

import enum
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


# Values match Bucket field names so `setattr(bucket, kind.value, ...)` resolves.
class CallBucket(enum.Enum):
    POSITIVE_ACCEPTED = "positive_accepted"
    NEGATIVE_REJECTED = "negative_rejected"
    POSITIVE_DRIFT = "positive_drift"
    NEGATIVE_DRIFT = "negative_drift"
    SERVER_ERROR = "server_error"
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
    server_error: int = 0
    route_rejected: int = 0
    auth_rejected: int = 0
    other: int = 0

    @property
    def total(self) -> int:
        return (
            self.positive_accepted
            + self.negative_rejected
            + self.positive_drift
            + self.negative_drift
            + self.server_error
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
            + self.server_error
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
        return self.positive_accepted + self.negative_rejected + self.server_error

    @property
    def useful_ratio(self) -> float:
        total = self.total
        return self.useful / total if total else 0.0


@dataclass(frozen=True, slots=True)
class FailureRef:
    check_name: str
    operation_label: str
    failure_type: str
    # Excluded from `fingerprint` so message variants collapse into one bucket; retained
    # here so a manual auditor can label "real bug" vs "false positive" without re-running.
    message: str

    @property
    def fingerprint(self) -> str:
        return f"{self.check_name}|{self.operation_label}|{self.failure_type}"


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
    # The harness computes covered/total because it has the spec; we surface only the numerator.
    covered_operations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RateMetrics:
    failures_per_minute: float = 0.0
    twoxx_per_minute: float = 0.0
    # Each row: {"minute": int, "covered": cumulative distinct ops with >= 1 2xx by that minute}.
    new_op_per_minute_timeline: list[dict] = field(default_factory=list)


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
class RunMetrics:
    schemathesis_version: str
    seed: int | None
    command: str
    duration_seconds: float
    buckets: Bucket = field(default_factory=Bucket)
    status_histogram: dict[int | str, int] = field(default_factory=dict)
    phases: list[PhaseMetrics] = field(default_factory=list)
    operations: dict[str, OperationMetrics] = field(default_factory=dict)
    failures: list[FailureRef] = field(default_factory=list)
    # Raw occurrence count, not deduped — complements `failures` for the
    # "N classes, M occurrences" view.
    failure_counts: dict[str, int] = field(default_factory=dict)
    # Stateful phase emits a synthetic label ("Stateful tests") rather than a real op;
    # aggregated separately so per-operation rankings stay focused on real operations.
    stateful: OperationMetrics | None = None
    mutations: MutationStats = field(default_factory=MutationStats)
    coverage_scenarios: CoverageScenarioStats = field(default_factory=CoverageScenarioStats)
    rates: RateMetrics = field(default_factory=RateMetrics)
    reachability: Reachability = field(default_factory=Reachability)


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
            return CallClassification(CallBucket.SERVER_ERROR)
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


def _operation_target(run: RunMetrics, label: str) -> OperationMetrics:
    if _is_stateful_label(label):
        if run.stateful is None:
            run.stateful = OperationMetrics(label=label)
        return run.stateful
    return run.operations.setdefault(label, OperationMetrics(label=label))


def _iter_calls(payload: dict) -> list[dict]:
    recorder = payload.get("recorder") or {}
    recorder_label = recorder.get("label") or ""
    cases = recorder.get("cases") or {}
    interactions = recorder.get("interactions") or {}
    calls: list[dict] = []
    for case_id, interaction in interactions.items():
        response = interaction.get("response") if isinstance(interaction, dict) else None
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
                "generation_time": generation_time,
                "elapsed": elapsed,
            }
        )
    return calls


def _attribution_label(recorder_label: str, case_value: dict) -> str:
    # Where the call/failure counts in the per-operation tables.
    # - Stateful: recorder.label is synthetic; group everything under it.
    # - Fuzz: recorder.label is synthetic; each case is on a real operation, attribute by that.
    # - Regular + Coverage: recorder.label is the declared op label; use it (coverage may
    #   mutate the case method but the call still belongs to the targeted operation).
    if _is_stateful_label(recorder_label):
        return recorder_label
    if _is_fuzz_label(recorder_label):
        return _case_operation_label(case_value) or recorder_label
    return recorder_label or _case_operation_label(case_value)


def _declared_label(recorder_label: str, case_value: dict) -> str:
    # Drives `_matches_operation_route`. Must be the *declared* operation label so the
    # check compares the case method against the spec method, not against itself:
    # - Coverage method-mutation cases set case.method=TRACE while the declared op is POST;
    #   the recorder label still says "POST /...", so the mismatch is detected (route_rejected).
    # - Synthetic recorder labels (Stateful/Fuzz) carry no spec method; per-case method+path
    #   is the real operation and method-mutation doesn't happen in those phases.
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


def _bump_bucket(bucket: Bucket, kind: CallBucket) -> None:
    field_name = kind.value
    setattr(bucket, field_name, getattr(bucket, field_name) + 1)


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
    out: list[FailureRef] = []
    for case_id, case_checks in (recorder.get("checks") or {}).items():
        if not isinstance(case_checks, list):
            continue
        case_value = (cases.get(case_id) or {}).get("value") or {}
        label = _attribution_label(recorder_label, case_value)
        for check in case_checks:
            if not isinstance(check, dict) or check.get("status") != "failure":
                continue
            failure = (check.get("failure_info") or {}).get("failure") or {}
            ftype = failure.get("type")
            if not ftype:
                continue
            out.append(
                FailureRef(
                    check_name=check.get("name") or "unknown",
                    operation_label=label,
                    failure_type=ftype,
                    message=failure.get("message", "") or "",
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
    # Subset of `covered_ops`: only ops where engine_started_at + per-event timestamps
    # are both present, which is what the timeline needs.
    first_2xx_minute: dict[str, int] = {}
    covered_ops: set[str] = set()
    twoxx_total = 0
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
            elif event_name == "EngineStarted" and isinstance(timestamp, (int, float)):
                engine_started_at = float(timestamp)
            elif event_name == "EngineFinished" and isinstance(timestamp, (int, float)):
                if engine_started_at is not None:
                    run.duration_seconds = max(0.0, float(timestamp) - engine_started_at)
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
                    _bump_bucket(run.buckets, result.bucket)
                    status = call["status"]
                    run.status_histogram[status] = run.status_histogram.get(status, 0) + 1
                    label = call["operation_label"]
                    if label:
                        operation = _operation_target(run, label)
                        _bump_bucket(operation.buckets, result.bucket)
                        if result.bucket is CallBucket.POSITIVE_DRIFT:
                            for location in result.locations_present:
                                operation.wasted_by_location[location] = (
                                    operation.wasted_by_location.get(location, 0) + 1
                                )
                        if call.get("generation_time") is not None:
                            operation.generation_seconds += call["generation_time"]
                        if call.get("elapsed") is not None:
                            operation.response_seconds += call["elapsed"]
                    is_2xx = isinstance(call["status"], int) and 200 <= call["status"] < 300
                    if is_2xx:
                        twoxx_total += 1
                    if is_2xx and label and not _is_stateful_label(label):
                        covered_ops.add(label)
                        if (
                            label not in first_2xx_minute
                            and engine_started_at is not None
                            and isinstance(timestamp, (int, float))
                        ):
                            first_2xx_minute[label] = int(max(0.0, float(timestamp) - engine_started_at) // 60)
                    if current_phase and current_phase in open_phases:
                        state = open_phases[current_phase]
                        _bump_bucket(state["buckets"], result.bucket)
                        if isinstance(timestamp, (int, float)):
                            state["last_seen"] = float(timestamp)
                        if call.get("generation_time") is not None:
                            state["generation_seconds"] += call["generation_time"]
                        if call.get("elapsed") is not None:
                            state["response_seconds"] += call["elapsed"]
                _accumulate_mutations(payload, run.mutations)
                _accumulate_coverage_scenarios(payload, run.coverage_scenarios)
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
    run.reachability.covered_operations = sorted(covered_ops)
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
        run.rates.new_op_per_minute_timeline.append({"minute": minute, "covered": cumulative})
