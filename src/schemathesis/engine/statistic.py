from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemathesis.baseline import failure_identity
from schemathesis.core.failures import BlockFormatter, Failure, format_failures
from schemathesis.core.result import Ok
from schemathesis.core.transforms import UNRESOLVABLE
from schemathesis.core.transport import Response
from schemathesis.engine import Status
from schemathesis.engine.recorder import CaseNode, RecordedScenario, ReproductionStep
from schemathesis.generation.case import Case
from schemathesis.generation.meta import coverage_scenario, report_label

if TYPE_CHECKING:
    from schemathesis.baseline import Baseline, Identity
    from schemathesis.config import OutputConfig
    from schemathesis.generation.stateful.state_machine import ExtractedParam, ExtractionFailure, Transition


@dataclass(slots=True)
class GroupedFailures:
    """Represents failures grouped by case ID."""

    # `None` for run-level checks (not tied to a case).
    case_id: str | None
    code_sample: str | None
    failures: list[Failure]
    response: Response | None
    auth_identity: str | None = None
    # Requests the failure needs, oldest first; empty when only the code sample is known.
    steps: list[ReproductionStep] = field(default_factory=list)

    def format(
        self,
        *,
        index: int | None,
        curl: str | None,
        config: OutputConfig,
        formatter: BlockFormatter | None = None,
        with_identity: bool = True,
    ) -> str:
        """Render the failures as text, headed by `<index>. Test Case ID: <case id>` when `index` is given."""
        header = f"{index}. Test Case ID: {self.case_id}" if index is not None and self.case_id is not None else None
        return format_failures(
            case_id=header,
            response=self.response,
            failures=self.failures,
            curl=curl,
            auth_identity=self.auth_identity if with_identity else None,
            formatter=formatter,
            config=config,
        )


def _collect_history(recorder: RecordedScenario, node: CaseNode, response: Response) -> list[tuple[Case, Response]]:
    history = [(node.value, response)]
    current = node
    while current.parent_id is not None:
        current_response = recorder.find_response(case_id=current.parent_id)
        # We need a response to get there, so it should be present
        assert current_response is not None
        current = recorder.cases[current.parent_id]
        history.append((current.value, current_response))
    return history


def _classify_extraction(extracted: ExtractedParam) -> tuple[bool, Exception | None]:
    if isinstance(extracted.value, Ok):
        return extracted.value.ok() is UNRESOLVABLE, None
    return True, extracted.value.err()


def _extraction_failure_inputs(transition: Transition) -> Iterator[tuple[str, str, Exception | None]]:
    if (
        transition.request_body is not None
        and isinstance(transition.request_body.value, Ok)
        and transition.request_body.value.ok() is UNRESOLVABLE
    ):
        yield "body", json.dumps(transition.request_body.definition), None

    for parameters in transition.parameters.values():
        for parameter, extracted in parameters.items():
            is_failure, error = _classify_extraction(extracted)
            if is_failure:
                yield parameter, extracted.definition, error


def _extraction_failures(recorder: RecordedScenario, case_id: str, node: CaseNode) -> Iterator[ExtractionFailure]:
    from schemathesis.generation.stateful.state_machine import ExtractionFailure

    transition = node.transition
    if transition is None or transition.is_inferred:
        return

    parent = recorder.cases[transition.parent_id]
    response = recorder.find_response(case_id=parent.value.id)
    # We need a response to get there, so it should be present
    assert response is not None
    history = None
    for parameter, expression, error in _extraction_failure_inputs(transition):
        # Built once per transition, and only when it has a failure to report.
        if history is None:
            history = _collect_history(recorder, parent, response)
        yield ExtractionFailure(
            id=transition.id,
            case_id=case_id,
            source=parent.value.operation.label,
            target=node.value.operation.label,
            parameter_name=parameter,
            expression=expression,
            history=history,
            response=response,
            error=error,
        )


@dataclass(slots=True)
class Statistic:
    """Running statistics about test execution."""

    failures: dict[str, dict[str, GroupedFailures]]
    # Track first case_id where each unique failure was found
    unique_failures_map: dict[Failure, str]

    # Failures the configured baseline already accounts for, and the entries they matched.
    baseline: Baseline | None
    # Identity -> matched entry id.
    known_failures: dict[Identity, str]
    # Identity -> the failure and the check that produced it, known or not.
    observed_failures: dict[Identity, tuple[Failure, str]]

    # Failures `filter_failure` hooks dropped across the run.
    filtered_failures: int

    # Sorted so the report order does not depend on hash randomization.
    extraction_failures: list[ExtractionFailure]

    tested_operations: set[str]
    errored_operations: set[str]
    # Operations whose cases ran but had no applicable checks.
    operations_without_checks: set[str]

    total_cases: int
    cases_with_failures: int
    cases_without_checks: int
    errored_cases: int

    def __init__(self) -> None:
        self.failures = {}
        self.unique_failures_map = {}
        self.baseline = None
        self.known_failures = {}
        self.observed_failures = {}
        self.filtered_failures = 0
        self.extraction_failures = []
        self.tested_operations = set()
        self.errored_operations = set()
        self.operations_without_checks = set()
        self.total_cases = 0
        self.cases_with_failures = 0
        self.cases_without_checks = 0
        self.errored_cases = 0

    @property
    def no_checks_ran(self) -> bool:
        """Cases ran, but no check applied to any of them."""
        return self.cases_without_checks > 0 and not self.tested_operations

    def on_scenario_finished(
        self, recorder: RecordedScenario, *, failure_label: Callable[[Case], str] | None = None
    ) -> None:
        """Update statistics and store failures from a new batch of checks."""
        # Track failures per-label (may differ from recorder.label when method differs)
        failures_by_label: dict[str, dict[str, GroupedFailures]] = {}

        self.total_cases += len(recorder.cases)
        self.filtered_failures += sum(recorder.filtered_failures.values())

        extraction_failures: set[ExtractionFailure] = set()
        scenario_errored = any(check.status == Status.ERROR for checks in recorder.checks.values() for check in checks)

        for case_id, case in recorder.cases.items():
            checks = recorder.checks.get(case_id, [])

            if any(check.status == Status.ERROR for check in checks):
                self.errored_cases += 1
                self.errored_operations.add(case.value.operation.label)
                continue

            if not checks:
                interaction = recorder.interactions.get(case_id)
                if scenario_errored or interaction is None or interaction.response is None:
                    self.errored_cases += 1
                else:
                    self.cases_without_checks += 1
                    self.operations_without_checks.add(case.value.operation.label)
                continue

            self.tested_operations.add(case.value.operation.label)
            current_case_failures = []
            last_failure_info = None

            for check in checks:
                if check.failure_info is not None:
                    failure = check.failure_info.failure

                    # Check if this is a new unique failure
                    identity = failure_identity(failure, check.name)
                    if failure not in self.unique_failures_map and identity not in self.known_failures:
                        self.observed_failures[identity] = (failure, check.name)
                        entry = self.baseline.match(failure, check.name) if self.baseline is not None else None
                        if entry is not None:
                            self.known_failures[identity] = entry.id
                            continue
                        last_failure_info = check.failure_info
                        self.unique_failures_map[failure] = case_id
                        current_case_failures.append(failure)
                    else:
                        # This failure was already seen - skip it
                        continue

            if current_case_failures:
                assert last_failure_info is not None
                label = report_label(
                    operation_label=failure_label(case.value) if failure_label is not None else recorder.label,
                    method=case.value.method,
                    path=case.value.path,
                    scenario=coverage_scenario(case.value),
                )
                failures_by_label.setdefault(label, {})[case_id] = GroupedFailures(
                    case_id=case_id,
                    code_sample=last_failure_info.code_sample,
                    steps=last_failure_info.steps,
                    failures=current_case_failures,
                    response=recorder.interactions[case_id].response,
                    auth_identity=case.value._auth_identity,
                )

            if current_case_failures:
                self.cases_with_failures += 1

            extraction_failures.update(_extraction_failures(recorder, case_id, case))

        for label, failures in failures_by_label.items():
            for group in failures.values():
                group.failures = sorted(set(group.failures))
            existing = self.failures.get(label, {})
            existing.update(failures)
            self.failures[label] = existing

        if extraction_failures:
            self.extraction_failures = sorted(
                set(self.extraction_failures).union(extraction_failures),
                key=lambda failure: (
                    failure.source,
                    failure.target,
                    failure.id,
                    failure.parameter_name,
                    failure.expression,
                ),
            )

    def record_run_check_failures(self, failures: list[Failure], *, label: str) -> None:
        """Store failures from class-based checks' `after_run`, which are not tied to any case."""
        for failure in failures:
            self.unique_failures_map[failure] = label
        self.failures.setdefault(label, {})[label] = GroupedFailures(
            case_id=None,
            code_sample=None,
            failures=sorted(set(failures)),
            response=None,
        )
