from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.checks import run_checks
from schemathesis.core.failures import FailureGroup
from schemathesis.engine._baseline import is_known

if TYPE_CHECKING:
    from schemathesis.baseline import Baseline
    from schemathesis.checks import CheckContext
    from schemathesis.core.failures import Failure
    from schemathesis.core.transport import Response
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.generation.case import Case


def validate_response(
    *,
    case: Case,
    ctx: CheckContext,
    response: Response,
    continue_on_failure: bool,
    recorder: ScenarioRecorder,
    baseline: Baseline | None = None,
) -> None:
    checked_by: dict[Failure, str] = {}

    def on_failure(name: str, collected: set[Failure], failure: Failure) -> None:
        collected.add(failure)
        checked_by.setdefault(failure, name)
        failure_data = recorder.find_failure_data(parent_id=case.id, failure=failure)
        recorder.record_check_failure(
            name=name,
            case_id=failure_data.case.id,
            code_sample=failure_data.case.as_curl_command(headers=failure_data.headers, verify=failure_data.verify),
            failure=failure,
        )

    def on_success(name: str, _case: Case) -> None:
        recorder.record_check_success(name=name, case_id=_case.id)

    failures = run_checks(
        case=case,
        response=response,
        ctx=ctx,
        checks=ctx._checks,
        on_failure=on_failure,
        on_success=on_success,
    )

    new = [failure for failure in failures if not is_known(failure, checked_by[failure], baseline)]
    if new and not continue_on_failure:
        raise FailureGroup(new) from None
