from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.core.failures import Failure
from schemathesis.core.result import Err, Ok
from schemathesis.core.transforms import UNRESOLVABLE
from schemathesis.core.transport import Response
from schemathesis.engine.recorder import CaseNode, ScenarioRecorder
from schemathesis.generation.case import Case
from schemathesis.generation.meta import CoveragePhaseData, CoverageScenario

if TYPE_CHECKING:
    from schemathesis.generation.stateful.state_machine import ExtractionFailure


@dataclass
class GroupedFailures:
    """Represents failures grouped by case ID."""

    case_id: str
    code_sample: str
    failures: list[Failure]
    response: Response | None

    __slots__ = ("case_id", "code_sample", "failures", "response")


@dataclass
class Statistic:
    """Running statistics about test execution."""

    failures: dict[str, dict[str, GroupedFailures]]
    # Track first case_id where each unique failure was found
    unique_failures_map: dict[Failure, str]

    extraction_failures: set[ExtractionFailure]

    tested_operations: set[str]

    total_cases: int
    cases_with_failures: int
    cases_without_checks: int

    __slots__ = (
        "failures",
        "unique_failures_map",
        "extraction_failures",
        "tested_operations",
        "total_cases",
        "cases_with_failures",
        "cases_without_checks",
    )

    def __init__(self) -> None:
        self.failures = {}
        self.unique_failures_map = {}
        self.extraction_failures = set()
        self.tested_operations = set()
        self.total_cases = 0
        self.cases_with_failures = 0
        self.cases_without_checks = 0

    def on_scenario_finished(self, recorder: ScenarioRecorder) -> None:
        """Update statistics and store failures from a new batch of checks."""
        from schemathesis.generation.stateful.state_machine import ExtractionFailure

        # Track failures per-label (may differ from recorder.label when method differs)
        failures_by_label: dict[str, dict[str, GroupedFailures]] = {}

        self.total_cases += len(recorder.cases)

        extraction_failures = set()

        def collect_history(node: CaseNode, response: Response) -> list[tuple[Case, Response]]:
            history = [(node.value, response)]
            current = node
            while current.parent_id is not None:
                current_response = recorder.find_response(case_id=current.parent_id)
                # We need a response to get there, so it should be present
                assert current_response is not None
                current = recorder.cases[current.parent_id]
                history.append((current.value, current_response))
            return history

        for case_id, case in recorder.cases.items():
            checks = recorder.checks.get(case_id, [])

            if not checks:
                self.cases_without_checks += 1
                continue

            self.tested_operations.add(case.value.operation.label)
            has_failures = False
            current_case_failures = []
            last_failure_info = None

            for check in checks:
                if check.failure_info is not None:
                    failure = check.failure_info.failure

                    # Check if this is a new unique failure
                    if failure not in self.unique_failures_map:
                        last_failure_info = check.failure_info
                        self.unique_failures_map[failure] = case_id
                        current_case_failures.append(failure)
                        has_failures = True
                    else:
                        # This failure was already seen - skip it
                        continue

            if current_case_failures:
                assert last_failure_info is not None
                # Compute correct label for UNSPECIFIED_HTTP_METHOD coverage scenario (GH-3322)
                meta = case.value._meta
                if (
                    meta is not None
                    and isinstance(meta.phase.data, CoveragePhaseData)
                    and meta.phase.data.scenario == CoverageScenario.UNSPECIFIED_HTTP_METHOD
                ):
                    label = f"{case.value.method} {case.value.path}"
                else:
                    label = recorder.label
                failures_by_label.setdefault(label, {})[case_id] = GroupedFailures(
                    case_id=case_id,
                    code_sample=last_failure_info.code_sample,
                    failures=current_case_failures,
                    response=recorder.interactions[case_id].response,
                )

            if has_failures:
                self.cases_with_failures += 1

            # Don't report extraction failures for inferred transitions
            if case.transition is None or case.transition.is_inferred:
                continue
            transition = case.transition
            parent = recorder.cases[transition.parent_id]
            response = recorder.find_response(case_id=parent.value.id)
            # We need a response to get there, so it should be present
            assert response is not None

            history = None

            if (
                transition.request_body is not None
                and isinstance(transition.request_body.value, Ok)
                and transition.request_body.value.ok() is UNRESOLVABLE
            ):
                history = collect_history(parent, response)
                extraction_failures.add(
                    ExtractionFailure(
                        id=transition.id,
                        case_id=case_id,
                        source=parent.value.operation.label,
                        target=case.value.operation.label,
                        parameter_name="body",
                        expression=json.dumps(transition.request_body.definition),
                        history=history,
                        response=response,
                        error=None,
                    )
                )

            for params in transition.parameters.values():
                for parameter, extracted in params.items():
                    if isinstance(extracted.value, Ok) and extracted.value.ok() is UNRESOLVABLE:
                        history = history or collect_history(parent, response)
                        extraction_failures.add(
                            ExtractionFailure(
                                id=transition.id,
                                case_id=case_id,
                                source=parent.value.operation.label,
                                target=case.value.operation.label,
                                parameter_name=parameter,
                                expression=extracted.definition,
                                history=history,
                                response=response,
                                error=None,
                            )
                        )
                    elif isinstance(extracted.value, Err):
                        history = history or collect_history(parent, response)
                        extraction_failures.add(
                            ExtractionFailure(
                                id=transition.id,
                                case_id=case_id,
                                source=parent.value.operation.label,
                                target=case.value.operation.label,
                                parameter_name=parameter,
                                expression=extracted.definition,
                                history=history,
                                response=response,
                                error=extracted.value.err(),
                            )
                        )

        for label, failures in failures_by_label.items():
            for group in failures.values():
                group.failures = sorted(set(group.failures))
            existing = self.failures.get(label, {})
            existing.update(failures)
            self.failures[label] = existing

        if extraction_failures:
            self.extraction_failures.update(extraction_failures)
