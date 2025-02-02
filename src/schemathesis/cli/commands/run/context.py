from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generator

from schemathesis.core.failures import Failure
from schemathesis.core.output import OutputConfig
from schemathesis.core.result import Err, Ok
from schemathesis.core.transforms import UNRESOLVABLE
from schemathesis.core.transport import Response
from schemathesis.engine import Status, events
from schemathesis.engine.recorder import CaseNode, ScenarioRecorder
from schemathesis.generation.case import Case

if TYPE_CHECKING:
    from schemathesis.generation.stateful.state_machine import ExtractionFailure


@dataclass
class Statistic:
    """Running statistics about test execution."""

    outcomes: dict[Status, int]
    failures: dict[str, dict[str, GroupedFailures]]

    extraction_failures: set[ExtractionFailure]

    tested_operations: set[str]

    total_cases: int
    cases_with_failures: int
    cases_without_checks: int

    __slots__ = (
        "outcomes",
        "failures",
        "extraction_failures",
        "tested_operations",
        "total_cases",
        "cases_with_failures",
        "cases_without_checks",
    )

    def __init__(self) -> None:
        self.outcomes = {}
        self.failures = {}
        self.extraction_failures = set()
        self.tested_operations = set()
        self.total_cases = 0
        self.cases_with_failures = 0
        self.cases_without_checks = 0

    def record_checks(self, recorder: ScenarioRecorder) -> None:
        """Update statistics and store failures from a new batch of checks."""
        from schemathesis.generation.stateful.state_machine import ExtractionFailure

        failures = self.failures.get(recorder.label, {})

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
            for check in checks:
                response = recorder.interactions[case_id].response

                # Collect failures
                if check.failure_info is not None:
                    has_failures = True
                    if case_id not in failures:
                        failures[case_id] = GroupedFailures(
                            case_id=case_id,
                            code_sample=check.failure_info.code_sample,
                            failures=[],
                            response=response,
                        )
                    failures[case_id].failures.append(check.failure_info.failure)
            if has_failures:
                self.cases_with_failures += 1

            if case.transition is None:
                continue
            transition = case.transition
            parent = recorder.cases[transition.parent_id]
            response = recorder.find_response(case_id=parent.value.id)
            # We need a response to get there, so it should be present
            assert response is not None

            for params in transition.parameters.values():
                for parameter, extracted in params.items():
                    if isinstance(extracted.value, Ok) and extracted.value.ok() is UNRESOLVABLE:
                        history = collect_history(parent, response)
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
                        history = collect_history(parent, response)
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

        if failures:
            for group in failures.values():
                group.failures = sorted(set(group.failures))
            self.failures[recorder.label] = failures

        if extraction_failures:
            self.extraction_failures.update(extraction_failures)


@dataclass
class GroupedFailures:
    """Represents failures grouped by case ID."""

    case_id: str
    code_sample: str
    failures: list[Failure]
    response: Response | None

    __slots__ = ("case_id", "code_sample", "failures", "response")


@dataclass
class ExecutionContext:
    """Storage for the current context of the execution."""

    statistic: Statistic = field(default_factory=Statistic)
    exit_code: int = 0
    output_config: OutputConfig = field(default_factory=OutputConfig)
    initialization_lines: list[str | Generator[str, None, None]] = field(default_factory=list)
    summary_lines: list[str | Generator[str, None, None]] = field(default_factory=list)
    seed: int | None = None

    def add_initialization_line(self, line: str | Generator[str, None, None]) -> None:
        self.initialization_lines.append(line)

    def add_summary_line(self, line: str | Generator[str, None, None]) -> None:
        self.summary_lines.append(line)

    def on_event(self, event: events.EngineEvent) -> None:
        if isinstance(event, events.ScenarioFinished):
            self.statistic.record_checks(event.recorder)
        elif isinstance(event, events.NonFatalError) or (
            isinstance(event, events.PhaseFinished)
            and event.phase.is_enabled
            and event.status in (Status.FAILURE, Status.ERROR)
        ):
            self.exit_code = 1
