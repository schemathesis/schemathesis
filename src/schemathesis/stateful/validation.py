from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.failures import Failure, FailureGroup

from ..internal.checks import CheckContext

if TYPE_CHECKING:
    from ..internal.checks import CheckFunction
    from ..models import Case
    from ..transports.responses import GenericResponse
    from .context import RunnerContext


def validate_response(
    *,
    response: GenericResponse,
    case: Case,
    runner_ctx: RunnerContext,
    check_ctx: CheckContext,
    checks: tuple[CheckFunction, ...],
    additional_checks: tuple[CheckFunction, ...] = (),
    max_response_time: int | None = None,
) -> None:
    """Validate the response against the provided checks."""
    from schemathesis.core.failures import ResponseTimeExceeded

    from ..runner.models import Check, Request, Response, Status

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
            response=Response.from_generic(response=response),
            case=copied_case,
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
            response=Response.from_generic(response=response),
            case=_case,
        )
        check_results.append(passed_check)

    for check in tuple(checks) + tuple(additional_checks):
        name = check.__name__
        copied_case = case.partial_deepcopy()
        try:
            skip_check = check(check_ctx, response, copied_case)
            if not skip_check:
                _on_passed(name, copied_case)
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

    if max_response_time:
        elapsed_time = response.elapsed.total_seconds() * 1000
        if elapsed_time > max_response_time:
            message = f"Actual: {elapsed_time:.2f}ms\nLimit: {max_response_time}.00ms"
            failure = ResponseTimeExceeded(
                operation=case.operation.verbose_name, message=message, elapsed=elapsed_time, deadline=max_response_time
            )
            if not runner_ctx.is_seen_in_run(failure):
                _on_failure(failure, "max_response_time")
        else:
            _on_passed("max_response_time", case)

    # Raise a grouped exception so Hypothesis can properly deduplicate it against the other failures
    if failures:
        raise FailureGroup(failures) from None
