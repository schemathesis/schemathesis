from __future__ import annotations

from typing import TYPE_CHECKING

from ..exceptions import CheckFailed, get_grouped_exception
from .context import RunnerContext

if TYPE_CHECKING:
    from ..failures import FailureContext
    from ..models import Case, CheckFunction
    from ..transports.responses import GenericResponse


def validate_response(
    *,
    response: GenericResponse,
    case: Case,
    ctx: RunnerContext,
    checks: tuple[CheckFunction, ...],
    additional_checks: tuple[CheckFunction, ...] = (),
    max_response_time: int | None = None,
) -> None:
    """Validate the response against the provided checks."""
    from .._compat import MultipleFailures
    from ..checks import _make_max_response_time_failure_message
    from ..failures import ResponseTimeExceeded
    from ..models import Check, Status

    exceptions: list[CheckFailed | AssertionError] = []
    check_results = ctx.checks_for_step

    def _on_failure(exc: CheckFailed | AssertionError, message: str, context: FailureContext | None) -> None:
        exceptions.append(exc)
        if ctx.is_seen_in_suite(exc):
            return
        failed_check = Check(
            name=name,
            value=Status.failure,
            response=response,
            elapsed=response.elapsed.total_seconds(),
            example=copied_case,
            message=message,
            context=context,
            request=None,
        )
        ctx.add_failed_check(failed_check)
        check_results.append(failed_check)
        ctx.mark_as_seen_in_suite(exc)

    def _on_passed(_name: str, _case: Case) -> None:
        passed_check = Check(
            name=_name,
            value=Status.success,
            response=response,
            elapsed=response.elapsed.total_seconds(),
            example=_case,
            request=None,
        )
        check_results.append(passed_check)

    for check in tuple(checks) + tuple(additional_checks):
        name = check.__name__
        copied_case = case.partial_deepcopy()
        try:
            check(response, copied_case)
            skip_check = check(response, copied_case)
            if not skip_check:
                _on_passed(name, copied_case)
        except CheckFailed as exc:
            if ctx.is_seen_in_run(exc):
                continue
            _on_failure(exc, str(exc), exc.context)
        except AssertionError as exc:
            if ctx.is_seen_in_run(exc):
                continue
            _on_failure(exc, str(exc) or f"Custom check failed: `{name}`", None)
        except MultipleFailures as exc:
            for subexc in exc.exceptions:
                if ctx.is_seen_in_run(subexc):
                    continue
                _on_failure(subexc, str(subexc), subexc.context)

    if max_response_time:
        elapsed_time = response.elapsed.total_seconds() * 1000
        if elapsed_time > max_response_time:
            message = _make_max_response_time_failure_message(elapsed_time, max_response_time)
            context = ResponseTimeExceeded(message=message, elapsed=elapsed_time, deadline=max_response_time)
            try:
                raise AssertionError(message)
            except AssertionError as _exc:
                if not ctx.is_seen_in_run(_exc):
                    _on_failure(_exc, message, context)
        else:
            _on_passed("max_response_time", case)

    # Raise a grouped exception so Hypothesis can properly deduplicate it against the other failures
    if exceptions:
        raise get_grouped_exception(case.operation.verbose_name, *exceptions)(causes=tuple(exceptions))
