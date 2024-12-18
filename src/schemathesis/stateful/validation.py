from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.checks import CheckContext, CheckFunction, run_checks
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.core.transport import Response

if TYPE_CHECKING:
    from schemathesis.generation.case import Case

    from .context import RunnerContext


def validate_response(
    *,
    response: Response,
    case: Case,
    runner_ctx: RunnerContext,
    check_ctx: CheckContext,
    checks: list[CheckFunction],
    additional_checks: tuple[CheckFunction, ...] = (),
) -> None:
    """Validate the response against the provided checks."""
    from ..runner.models import Check, Request, Status

    results = runner_ctx.checks_for_step

    def on_failure(name: str, collected: set[Failure], failure: Failure) -> None:
        if runner_ctx.is_seen_in_suite(failure) or runner_ctx.is_seen_in_run(failure):
            return
        failed_check = Check(
            name=name,
            status=Status.FAILURE,
            request=Request.from_prepared_request(response.request),
            response=response,
            case=case,
            failure=failure,
        )
        results.append(failed_check)
        runner_ctx.add_failed_check(failed_check)
        runner_ctx.mark_as_seen_in_suite(failure)
        collected.add(failure)

    def on_success(name: str, case: Case) -> None:
        results.append(
            Check(
                name=name,
                status=Status.SUCCESS,
                request=Request.from_prepared_request(response.request),
                response=response,
                case=case,
            )
        )

    failures = run_checks(
        case=case,
        response=response,
        ctx=check_ctx,
        checks=tuple(checks) + tuple(additional_checks),
        on_failure=on_failure,
        on_success=on_success,
    )

    if failures:
        raise FailureGroup(list(failures)) from None
