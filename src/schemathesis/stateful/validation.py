from __future__ import annotations

from typing import TYPE_CHECKING

from ..exceptions import CheckFailed, get_grouped_exception
from .context import RunnerContext

if TYPE_CHECKING:
    from ..failures import FailureContext
    from ..models import Case, CheckFunction, Check
    from ..transports.responses import GenericResponse


def validate_response(
    response: GenericResponse,
    case: Case,
    failures: RunnerContext,
    checks: tuple[CheckFunction, ...],
    check_results: list[Check],
    additional_checks: tuple[CheckFunction, ...] = (),
) -> None:
    """Validate the response against the provided checks."""
    from .._compat import MultipleFailures
    from ..models import Check, Status

    exceptions: list[CheckFailed | AssertionError] = []

    def _on_failure(exc: CheckFailed | AssertionError, message: str, context: FailureContext | None) -> None:
        exceptions.append(exc)
        if failures.is_seen_in_suite(exc):
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
        failures.add_failed_check(failed_check)
        check_results.append(failed_check)
        failures.mark_as_seen_in_suite(exc)

    for check in checks + additional_checks:
        name = check.__name__
        copied_case = case.partial_deepcopy()
        try:
            check(response, copied_case)
            skip_check = check(response, copied_case)
            if not skip_check:
                passed_check = Check(
                    name=name,
                    value=Status.success,
                    response=response,
                    elapsed=response.elapsed.total_seconds(),
                    example=copied_case,
                    request=None,
                )
                check_results.append(passed_check)
        except CheckFailed as exc:
            if failures.is_seen_in_run(exc):
                continue
            _on_failure(exc, str(exc), exc.context)
        except AssertionError as exc:
            if failures.is_seen_in_run(exc):
                continue
            _on_failure(exc, str(exc) or f"Custom check failed: `{name}`", None)
        except MultipleFailures as exc:
            for subexc in exc.exceptions:
                if failures.is_seen_in_run(subexc):
                    continue
                _on_failure(subexc, str(subexc), subexc.context)

    # Raise a grouped exception so Hypothesis can properly deduplicate it against the other failures
    if exceptions:
        raise get_grouped_exception(case.operation.verbose_name, *exceptions)(causes=tuple(exceptions))
