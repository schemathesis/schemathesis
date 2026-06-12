from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

from schemathesis.core import NOT_SET
from schemathesis.core.media_types import is_json
from schemathesis.core.result import Err, Ok, Result
from schemathesis.core.transforms import UNRESOLVABLE
from schemathesis.reporting.crashes import CrashCheck, CrashFile, CrashLink, CrashStep

if TYPE_CHECKING:
    import requests

    from schemathesis.checks import CheckContext
    from schemathesis.core.failures import Failure
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.generation.stateful.state_machine import StepOutput
    from schemathesis.schemas import APIOperation, BaseSchema


class ReplayStatus(str, Enum):
    FIXED = "fixed"
    FAILED = "failed"
    CHANGED = "changed"
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
    message: str = ""


@dataclass(slots=True)
class ReplayOutcome:
    status: ReplayStatus
    actual_status: int | None
    actual_body: str
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
    session: requests.Session,
    schema: BaseSchema | None = None,
) -> ReplayOutcome:
    operation = _resolve_operation(schema, crash) if schema is not None else None

    sequence = crash.sequence
    if len(sequence) > 1:
        return _replay_stateful(sequence, base_url=base_url, session=session, schema=schema, operation=operation)

    step = sequence[0]
    url = _resolve_url(step, base_url=base_url)

    t0 = time.monotonic()
    result = _send_step(session, step=step, url=url)
    elapsed = int((time.monotonic() - t0) * 1000)

    if isinstance(result, Err):
        return ReplayOutcome(
            status=ReplayStatus.ERRORED,
            actual_status=None,
            actual_body="",
            duration_ms=elapsed,
            error_message=str(result.err()),
        )

    response = result.ok()
    check_result = _evaluate_checks(
        step=step,
        response=response,
        operation=operation,
        session=session,
    )
    status = _case_status(check_result.outcomes) if check_result.outcomes else ReplayStatus.CHANGED

    return ReplayOutcome(
        status=status,
        actual_status=response.status_code,
        actual_body=response.text,
        duration_ms=elapsed,
        step_outcomes=[StepOutcome(status_code=response.status_code, body=response.text)],
        check_outcomes=check_result.outcomes,
        failures=check_result.failures,
        transport_response=check_result.transport_response,
    )


def _replay_stateful(
    sequence: list[CrashStep],
    *,
    base_url: str | None,
    session: requests.Session,
    schema: BaseSchema | None,
    operation: APIOperation | None,
) -> ReplayOutcome:
    step_outcomes: list[StepOutcome] = []
    t0 = time.monotonic()
    previous_response: requests.Response | None = None
    previous_step_output: StepOutput | None = None

    for index, step in enumerate(sequence):
        url = _resolve_url(step, base_url=base_url)

        if index > 0 and previous_response is not None:
            prev_link = sequence[index - 1].link
            if prev_link is not None and prev_link.parameters:
                try:
                    url = _substitute_link_parameters(
                        url, prev_link, previous_response, previous_step_output=previous_step_output
                    )
                except (KeyError, ValueError) as exc:
                    elapsed = int((time.monotonic() - t0) * 1000)
                    return ReplayOutcome(
                        status=ReplayStatus.ERRORED,
                        actual_status=None,
                        actual_body="",
                        duration_ms=elapsed,
                        error_message=f"extraction failed at step {index + 1} - {exc}",
                        step_outcomes=step_outcomes,
                    )

        result = _send_step(session, step=step, url=url)
        if isinstance(result, Err):
            elapsed = int((time.monotonic() - t0) * 1000)
            return ReplayOutcome(
                status=ReplayStatus.ERRORED,
                actual_status=None,
                actual_body="",
                duration_ms=elapsed,
                error_message=str(result.err()),
                step_outcomes=step_outcomes,
            )

        response = result.ok()
        step_outcomes.append(StepOutcome(status_code=response.status_code, body=response.text))
        previous_response = response
        previous_step_output = _build_step_output(schema, step, response, session)

    elapsed = int((time.monotonic() - t0) * 1000)
    terminal = sequence[-1]
    last_outcome = step_outcomes[-1] if step_outcomes else None

    if last_outcome is None:
        return ReplayOutcome(
            status=ReplayStatus.ERRORED,
            actual_status=None,
            actual_body="",
            duration_ms=elapsed,
            error_message="no response from terminal step",
        )

    assert previous_response is not None
    check_result = _evaluate_checks(
        step=terminal,
        response=previous_response,
        operation=operation,
        session=session,
    )
    status = _case_status(check_result.outcomes) if check_result.outcomes else ReplayStatus.CHANGED

    return ReplayOutcome(
        status=status,
        actual_status=last_outcome.status_code,
        actual_body=last_outcome.body,
        duration_ms=elapsed,
        step_outcomes=step_outcomes,
        check_outcomes=check_result.outcomes,
        failures=check_result.failures,
        transport_response=check_result.transport_response,
    )


@dataclass(slots=True)
class CheckResult:
    outcomes: list[CheckOutcome]
    failures: list[Failure]
    transport_response: Response | None


def _evaluate_checks(
    *,
    step: CrashStep,
    response: requests.Response,
    operation: APIOperation | None,
    session: requests.Session,
) -> CheckResult:
    if operation is None:
        return CheckResult(
            outcomes=_evaluate_checks_without_schema(step=step, response=response),
            failures=[],
            transport_response=None,
        )

    from schemathesis.checks import run_checks
    from schemathesis.core.transport import Response

    case = _build_case(operation, step)
    transport_response = Response.from_requests(response, verify=bool(session.verify))
    check_context = _make_check_context(operation)

    recorded_names = {check.name for check in step.checks}
    checks_to_run = [c for c in check_context._checks if c.__name__ in recorded_names]

    failed_check_names: set[str] = set()
    all_failures: list[Failure] = []

    def on_failure(name: str, collected: set, failure: Failure) -> None:
        collected.add(failure)
        failed_check_names.add(name)
        all_failures.append(failure)

    run_checks(
        case=case,
        response=transport_response,
        ctx=check_context,
        checks=checks_to_run,
        on_failure=on_failure,
    )

    check_outcomes: list[CheckOutcome] = []
    for check in step.checks:
        if check.name in failed_check_names:
            check_outcomes.append(CheckOutcome(name=check.name, status=ReplayStatus.FAILED))
        else:
            check_outcomes.append(
                CheckOutcome(
                    name=check.name,
                    status=ReplayStatus.FIXED,
                    note=f"{step.response_status} -> {response.status_code}",
                )
            )

    return CheckResult(outcomes=check_outcomes, failures=all_failures, transport_response=transport_response)


def _evaluate_checks_without_schema(
    *,
    step: CrashStep,
    response: requests.Response,
) -> list[CheckOutcome]:
    return _build_check_outcomes(
        checks=step.checks,
        original_status=step.response_status,
        actual_status=response.status_code,
        original_body=step.response_body,
        actual_body=response.text,
        content_type=step.response_headers.get("content-type", ""),
    )


def _make_check_context(operation: APIOperation) -> CheckContext:
    from schemathesis.checks import CheckContext

    return CheckContext(
        override=None,
        auth=None,
        headers=None,
        config=operation.schema.config.checks,
        transport_kwargs=None,
    )


def _resolve_operation(schema: BaseSchema, crash: CrashFile) -> APIOperation | None:
    try:
        return schema[crash.path_template][crash.method]
    except KeyError:
        return None


def _build_case(operation: APIOperation, step: CrashStep) -> Case:
    from schemathesis.generation.meta import CaseMetadata

    body = _decode_body(step.request_body) if step.request_body is not None else NOT_SET
    case = operation.Case(
        method=step.method,
        path_parameters={},
        query={},
        headers=step.request_headers,
        cookies={},
        body=body,
    )
    if step.meta is not None:
        case._meta = CaseMetadata.from_dict(step.meta)
    object.__setattr__(case, "_freeze_metadata", True)
    return case


def _resolve_url(step: CrashStep, *, base_url: str | None) -> str:
    url = step.url or step.url_template
    if base_url:
        url = _override_base_url(url, base_url)
    return url


def _send_step(
    session: requests.Session, *, step: CrashStep, url: str
) -> Result[requests.Response, requests.RequestException]:
    import requests

    try:
        return Ok(
            session.request(
                method=step.method,
                url=url,
                headers=step.request_headers,
                data=_decode_body(step.request_body),
                timeout=30,
                allow_redirects=True,
            )
        )
    except requests.RequestException as exc:
        return Err(exc)


def _substitute_link_parameters(
    url_template: str,
    link: CrashLink,
    previous_response: requests.Response,
    *,
    previous_step_output: StepOutput | None,
) -> str:

    substitutions: dict[str, str] = {}

    for key, expression in link.parameters.items():
        parts = key.split(".", 1)
        if len(parts) != 2:
            continue
        _location, param_name = parts

        value = _evaluate_link_expression(expression, previous_response, previous_step_output)
        if value is UNRESOLVABLE:
            raise ValueError(f"{expression} not found in step response")
        substitutions[param_name] = str(value)

    if substitutions:
        url_template = url_template.format_map(substitutions)
    return url_template


def _evaluate_link_expression(expression: str, response: requests.Response, step_output: StepOutput | None) -> Any:
    if step_output is not None:
        from schemathesis.specs.openapi.expressions import evaluate

        return evaluate(expression, step_output)

    if expression.startswith("$response.body#/"):
        pointer = expression[len("$response.body#/") :]
        try:
            body = response.json()
        except (ValueError, TypeError):
            return UNRESOLVABLE
        current = body
        for part in pointer.split("/"):
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list):
                try:
                    current = current[int(part)]
                except (IndexError, ValueError):
                    return UNRESOLVABLE
            else:
                return UNRESOLVABLE
        return current

    if expression.startswith("$response.header."):
        header_name = expression[len("$response.header.") :]
        value = response.headers.get(header_name)
        return UNRESOLVABLE if value is None else value

    if expression == "$response.body":
        return response.text

    return expression


def _build_step_output(
    schema: BaseSchema | None,
    step: CrashStep,
    response: requests.Response,
    session: requests.Session,
) -> StepOutput | None:
    if schema is None:
        return None
    from schemathesis.core.transport import Response
    from schemathesis.generation.stateful.state_machine import StepOutput

    path = urlparse(step.url_template or step.url).path
    try:
        operation = schema[path][step.method]
    except KeyError:
        return None
    case = _build_case(operation, step)
    transport_response = Response.from_requests(response, verify=bool(session.verify))
    return StepOutput(response=transport_response, case=case)


def _override_base_url(url: str, base_url: str) -> str:
    parsed = urlparse(url)
    base = urlparse(base_url)
    return urlunparse(parsed._replace(scheme=base.scheme, netloc=base.netloc))


def _decode_body(raw: str | None) -> bytes | None:
    if raw is None:
        return None
    return base64.b64decode(raw)


def _build_check_outcomes(
    *,
    checks: list[CrashCheck],
    original_status: int,
    actual_status: int,
    original_body: str,
    actual_body: str,
    content_type: str,
) -> list[CheckOutcome]:
    check_outcomes: list[CheckOutcome] = []
    for check in checks:
        check_name = check.name
        if check_name == "not_a_server_error":
            if actual_status < 500:
                check_outcomes.append(
                    CheckOutcome(
                        name=check_name, status=ReplayStatus.FIXED, note=f"{original_status} -> {actual_status}"
                    )
                )
            elif actual_status == original_status:
                check_outcomes.append(CheckOutcome(name=check_name, status=ReplayStatus.FAILED))
            else:
                check_outcomes.append(
                    CheckOutcome(
                        name=check_name, status=ReplayStatus.CHANGED, note=f"{original_status} -> {actual_status}"
                    )
                )
        else:
            if actual_status == original_status and bodies_equal(actual_body, original_body, content_type=content_type):
                check_outcomes.append(CheckOutcome(name=check_name, status=ReplayStatus.FAILED))
            else:
                check_outcomes.append(
                    CheckOutcome(
                        name=check_name, status=ReplayStatus.CHANGED, note=f"{original_status} -> {actual_status}"
                    )
                )
    return check_outcomes


def _case_status(check_outcomes: list[CheckOutcome]) -> ReplayStatus:
    if all(c.status is ReplayStatus.FIXED for c in check_outcomes):
        return ReplayStatus.FIXED
    if any(c.status is ReplayStatus.FAILED for c in check_outcomes):
        return ReplayStatus.FAILED
    return ReplayStatus.CHANGED


def bodies_equal(left: str, right: str, *, content_type: str) -> bool:
    if left == right:
        return True
    if content_type and is_json(content_type):
        try:
            return json.loads(left) == json.loads(right)
        except (ValueError, TypeError):
            pass
    return False
