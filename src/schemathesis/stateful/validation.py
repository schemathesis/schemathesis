from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.checks import CheckContext, CheckFunction
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.core.transport import Response

if TYPE_CHECKING:
    from ..models import Case
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

    failures: list[Failure] = []
    check_results = runner_ctx.checks_for_step

    def _on_failure(failure: Failure, _name: str) -> None:
        failures.append(failure)
        if runner_ctx.is_seen_in_suite(failure):
            return
        failed_check = Check(
            name=_name,
            status=Status.failure,
            request=Request.from_prepared_request(response.request),
            response=response,
            case=case,
            failure=failure,
        )
        runner_ctx.add_failed_check(failed_check)
        check_results.append(failed_check)
        runner_ctx.mark_as_seen_in_suite(failure)

    def _on_passed(_name: str, _case: Case) -> None:
        passed_check = Check(
            name=_name,
            status=Status.success,
            request=Request.from_prepared_request(response.request),
            response=response,
            case=_case,
        )
        check_results.append(passed_check)

    for check in tuple(checks) + tuple(additional_checks):
        name = check.__name__
        try:
            skip_check = check(check_ctx, response, case)
            if not skip_check:
                _on_passed(name, case)
        except Failure as exc:
            if runner_ctx.is_seen_in_run(exc):
                continue
            _on_failure(exc, name)
        except AssertionError as exc:
            failure = Failure.from_assertion(name=name, operation=case.operation.verbose_name, exc=exc)
            if runner_ctx.is_seen_in_run(failure):
                continue
            _on_failure(failure, name)
        except FailureGroup as exc:
            for subexc in exc.exceptions:
                if runner_ctx.is_seen_in_run(subexc):
                    continue
                _on_failure(subexc, name)

    if failures:
        raise FailureGroup(failures) from None
