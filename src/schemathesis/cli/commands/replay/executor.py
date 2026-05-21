from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from schemathesis.core.media_types import is_json
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.timing import Instant
from schemathesis.core.transforms import UNRESOLVABLE
from schemathesis.reporting.crashes import CrashCheck, CrashFile, CrashLink, CrashStep

if TYPE_CHECKING:
    import requests

    from schemathesis.checks import CheckContext
    from schemathesis.config import ProjectConfig
    from schemathesis.core.failures import Failure
    from schemathesis.core.transport import Response
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.generation.case import Case
    from schemathesis.generation.stateful.state_machine import StepOutput
    from schemathesis.schemas import APIOperation, BaseSchema


class ReplayStatus(Enum):
    FIXED = "fixed"
    FAILED = "failed"
    ERRORED = "errored"


@dataclass(slots=True)
class StepOutcome:
    status_code: int
    body: str


@dataclass(slots=True)
class CheckOutcome:
    name: str
    status: ReplayStatus
    note: str = ""


@dataclass(slots=True)
class ReplayOutcome:
    status: ReplayStatus
    duration_ms: int = 0
    error_message: str = ""
    step_outcomes: list[StepOutcome] = field(default_factory=list)
    check_outcomes: list[CheckOutcome] = field(default_factory=list)
    failures: list[Failure] = field(default_factory=list)
    transport_response: Response | None = None


def replay_crash_file(
    crash: CrashFile,
    *,
    base_url: str | None = None,
    project_config: ProjectConfig,
    schema: BaseSchema,
) -> ReplayOutcome:
    from schemathesis.engine.context import make_session

    operation = _resolve_operation(schema, crash)
    # A fresh session per crash: applies the operation's auth/headers/TLS config and isolates cookie state.
    session = make_session(project_config, operation=operation)
    try:
        return _replay_sequence(crash.sequence, base_url=base_url, session=session, schema=schema, operation=operation)
    finally:
        session.close()


def _errored_outcome(
    error_message: str, *, elapsed: int, step_outcomes: list[StepOutcome] | None = None
) -> ReplayOutcome:
    return ReplayOutcome(
        status=ReplayStatus.ERRORED,
        duration_ms=elapsed,
        error_message=error_message,
        step_outcomes=step_outcomes or [],
    )


def _step_operation(schema: BaseSchema, step: CrashStep) -> APIOperation | None:
    try:
        return schema[step.path][step.method]
    except LookupError:
        return None


def _replay_sequence(
    sequence: list[CrashStep],
    *,
    base_url: str | None,
    session: requests.Session,
    schema: BaseSchema,
    operation: APIOperation | None,
) -> ReplayOutcome:
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.generation.stateful.state_machine import StepOutput

    step_outcomes: list[StepOutcome] = []
    instant = Instant()

    # Record the replayed steps so history-dependent checks see the same sequence, each linked to its parent.
    recorder = ScenarioRecorder(label="replay")
    step_outputs: list[StepOutput] = []

    terminal_case: Case | None = None
    terminal_response: Response | None = None
    last_index = len(sequence) - 1

    for index, step in enumerate(sequence):
        step_operation = operation if index == last_index else _step_operation(schema, step)
        if step_operation is None:
            return _errored_outcome(
                f"operation not found in schema at step {index + 1}",
                elapsed=instant.elapsed_ms,
                step_outcomes=step_outcomes,
            )

        try:
            case = _build_case(step_operation, step)
        except Exception:
            # The recorded data (typically `meta`) can't be decoded - a corrupt file or a different version.
            return _errored_outcome(
                f"step {index + 1}: incompatible or corrupted crash data",
                elapsed=instant.elapsed_ms,
                step_outcomes=step_outcomes,
            )

        # A link extracts from its recorded parent step, not always the previous one (older crashes lack it).
        parent_output: StepOutput | None = None
        if step.parent_index is not None:
            if step.parent_index < len(step_outputs):
                parent_output = step_outputs[step.parent_index]
        elif step_outputs:
            parent_output = step_outputs[-1]

        if (
            parent_output is not None
            and step.link is not None
            and (step.link.parameters or step.link.request_body is not None)
        ):
            try:
                _apply_link_parameters(case, step.link, parent_output)
            except (KeyError, ValueError) as exc:
                return _errored_outcome(
                    f"extraction failed at step {index + 1} - {exc}",
                    elapsed=instant.elapsed_ms,
                    step_outcomes=step_outcomes,
                )

        try:
            response = case.call(base_url=base_url, session=session)
        except Exception as exc:
            return _errored_outcome(str(exc), elapsed=instant.elapsed_ms, step_outcomes=step_outcomes)

        parent_id = parent_output.case.id if parent_output is not None else None
        recorder.record_case(parent_id=parent_id, case=case, transition=None, is_transition_applied=False)
        recorder.record_response(case_id=case.id, response=response)

        step_outcomes.append(StepOutcome(status_code=response.status_code, body=response.text_lossy()))
        step_outputs.append(StepOutput(response=response, case=case))
        if index == last_index:
            terminal_case = case
            terminal_response = response

    elapsed = instant.elapsed_ms
    assert terminal_case is not None and terminal_response is not None
    terminal = sequence[-1]
    try:
        check_outcomes, check_failures = _evaluate_checks(
            case=terminal_case,
            response=terminal_response,
            recorded_checks=terminal.checks,
            recorder=recorder,
        )
    except Exception as exc:
        return _errored_outcome(f"check evaluation failed - {exc}", elapsed=elapsed, step_outcomes=step_outcomes)
    if not check_outcomes:
        return _errored_outcome("recorded no checks to verify", elapsed=elapsed, step_outcomes=step_outcomes)
    status = _case_status(check_outcomes)

    return ReplayOutcome(
        status=status,
        duration_ms=elapsed,
        step_outcomes=step_outcomes,
        check_outcomes=check_outcomes,
        failures=check_failures,
        transport_response=terminal_response,
    )


def _apply_link_parameters(case: Case, link: CrashLink, previous_step_output: StepOutput) -> None:
    from schemathesis.specs.openapi.expressions import evaluate

    # Re-extract each link parameter from the previous response into its container.
    for key, expression in link.parameters.items():
        location, _, name = key.partition(".")
        value = evaluate(expression, previous_step_output)
        if value is UNRESOLVABLE:
            # Re-extraction failed; reuse the value captured at the original failure.
            continue
        container = getattr(case, ParameterLocation(location).container_name)
        container[name] = value

    if link.request_body is not None:
        # Re-extract the body from the previous response, merging into the recorded one.
        body = evaluate(link.request_body, previous_step_output, evaluate_nested=True)
        if body is not UNRESOLVABLE:
            if isinstance(body, dict) and isinstance(case.body, dict):
                case.body = {**case.body, **body}
            else:
                case.body = body


def _evaluate_checks(
    *,
    case: Case,
    response: Response,
    recorded_checks: list[CrashCheck],
    recorder: ScenarioRecorder,
) -> tuple[list[CheckOutcome], list[Failure]]:
    from schemathesis.checks import CHECKS, is_check_class, load_all_checks, run_checks

    # Re-run exactly the recorded checks, ignoring the current enabled set; class-based ones report as unavailable.
    load_all_checks()
    registry = {check.__name__: check for check in CHECKS.get_all() if not is_check_class(check)}
    check_context = _make_check_context(case.operation, recorder)

    runnable = {check.name: registry[check.name] for check in recorded_checks if check.name in registry}

    failed_check_names: set[str] = set()
    all_failures: list[Failure] = []

    def on_failure(name: str, collected: set, failure: Failure) -> None:
        failed_check_names.add(name)
        all_failures.append(failure)

    run_checks(
        case=case,
        response=response,
        ctx=check_context,
        checks=list(runnable.values()),
        on_failure=on_failure,
    )

    check_outcomes: list[CheckOutcome] = []
    for check in recorded_checks:
        if check.name not in runnable:
            check_outcomes.append(
                CheckOutcome(name=check.name, status=ReplayStatus.ERRORED, note="check not available to re-run")
            )
        elif check.name in failed_check_names:
            check_outcomes.append(CheckOutcome(name=check.name, status=ReplayStatus.FAILED))
        else:
            check_outcomes.append(CheckOutcome(name=check.name, status=ReplayStatus.FIXED))

    # Order failures by severity so replay output matches the run.
    return check_outcomes, sorted(set(all_failures))


def _make_check_context(operation: APIOperation, recorder: ScenarioRecorder) -> CheckContext:
    from schemathesis.checks import CheckContext

    return CheckContext(
        override=None,
        auth=None,
        headers=None,
        config=operation.schema.config.checks_config_for(operation=operation),
        transport_kwargs=None,
        recorder=recorder,
        response_checks=None,
    )


def _resolve_operation(schema: BaseSchema, crash: CrashFile) -> APIOperation | None:
    try:
        path_map = schema[crash.path_template]
    except LookupError:
        return None
    try:
        return path_map[crash.method]
    except LookupError:
        # No declared operation for this method; borrow any on the path and replay the recorded method.
        return next(iter(path_map.values()), None)


# Headers the transport recomputes; replaying recorded values would misframe the request.
_STALE_HEADERS = frozenset({"content-length", "host", "transfer-encoding", "connection", "content-encoding", "cookie"})


def _build_case(operation: APIOperation, step: CrashStep) -> Case:
    from schemathesis.generation.meta import CaseMetadata

    source_headers = step.case_headers or step.request_headers
    headers = {key: value for key, value in source_headers.items() if key.lower() not in _STALE_HEADERS}
    case = operation.Case(
        method=step.method,
        path_parameters=step.path_parameters,
        query=step.query,
        headers=headers,
        cookies=step.cookies,
        body=step.case_body,
        media_type=step.media_type,
    )
    if step.meta is not None:
        case._meta = CaseMetadata.from_dict(step.meta)
    object.__setattr__(case, "_freeze_metadata", True)
    return case


def _case_status(check_outcomes: list[CheckOutcome]) -> ReplayStatus:
    if any(c.status is ReplayStatus.FAILED for c in check_outcomes):
        return ReplayStatus.FAILED
    if any(c.status is ReplayStatus.ERRORED for c in check_outcomes):
        return ReplayStatus.ERRORED
    return ReplayStatus.FIXED


def bodies_equal(left: str, right: str, *, content_type: str) -> bool:
    if left == right:
        return True
    if content_type and is_json(content_type):
        try:
            return json.loads(left) == json.loads(right)
        except (ValueError, TypeError):
            pass
    return False
