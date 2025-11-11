from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from typing import TYPE_CHECKING, Any

from schemathesis.config import ChecksConfig
from schemathesis.core.failures import (
    CustomFailure,
    Failure,
    FailureGroup,
    MalformedJson,
    ResponseTimeExceeded,
    ServerError,
)
from schemathesis.core.registries import Registry
from schemathesis.core.transport import Response
from schemathesis.generation.overrides import Override

if TYPE_CHECKING:
    from requests.models import CaseInsensitiveDict

    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.generation.case import Case

CheckFunction = Callable[["CheckContext", "Response", "Case"], bool | None]


class CheckContext:
    """Runtime context passed to validation check functions during API testing.

    Provides access to configuration for currently checked endpoint.
    """

    _override: Override | None
    _auth: tuple[str, str] | None
    _headers: CaseInsensitiveDict | None
    config: ChecksConfig
    """Configuration settings for validation checks."""
    _transport_kwargs: dict[str, Any] | None
    _recorder: ScenarioRecorder | None
    _checks: list[CheckFunction]

    __slots__ = ("_override", "_auth", "_headers", "config", "_transport_kwargs", "_recorder", "_checks")

    def __init__(
        self,
        override: Override | None,
        auth: tuple[str, str] | None,
        headers: CaseInsensitiveDict | None,
        config: ChecksConfig,
        transport_kwargs: dict[str, Any] | None,
        recorder: ScenarioRecorder | None = None,
    ) -> None:
        self._override = override
        self._auth = auth
        self._headers = headers
        self.config = config
        self._transport_kwargs = transport_kwargs
        self._recorder = recorder
        self._checks = []
        for check in CHECKS.get_all():
            name = check.__name__
            if self.config.get_by_name(name=name).enabled:
                self._checks.append(check)
        if self.config.max_response_time.enabled:
            self._checks.append(max_response_time)

    def _find_parent(self, *, case_id: str) -> Case | None:
        if self._recorder is not None:
            return self._recorder.find_parent(case_id=case_id)
        return None

    def _find_related(self, *, case_id: str) -> Iterator[Case]:
        if self._recorder is not None:
            yield from self._recorder.find_related(case_id=case_id)

    def _find_response(self, *, case_id: str) -> Response | None:
        if self._recorder is not None:
            return self._recorder.find_response(case_id=case_id)
        return None

    def _record_case(self, *, parent_id: str, case: Case) -> None:
        if self._recorder is not None:
            self._recorder.record_case(parent_id=parent_id, case=case, transition=None, is_transition_applied=False)

    def _record_response(self, *, case_id: str, response: Response) -> None:
        if self._recorder is not None:
            self._recorder.record_response(case_id=case_id, response=response)


CHECKS = Registry[CheckFunction]()


def load_all_checks() -> None:
    # NOTE: Trigger registering all Open API checks
    from schemathesis.specs.openapi.checks import status_code_conformance  # noqa: F401


def check(func: CheckFunction) -> CheckFunction:
    """Register a custom validation check to run against API responses.

    Args:
        func: Function that takes `(ctx: CheckContext, response: Response, case: Case)` and raises `AssertionError` on validation failure

    Example:
        ```python
        import schemathesis

        @schemathesis.check
        def check_cors_headers(ctx, response, case):
            \"\"\"Verify CORS headers are present\"\"\"
            if "Access-Control-Allow-Origin" not in response.headers:
                raise AssertionError("Missing CORS headers")
        ```

    """
    return CHECKS.register(func)


@check
def not_a_server_error(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    """A check to verify that the response is not a server-side error."""
    from schemathesis.specs.graphql.schemas import GraphQLSchema
    from schemathesis.specs.graphql.validation import validate_graphql_response
    from schemathesis.specs.openapi.utils import expand_status_codes

    expected_statuses = expand_status_codes(ctx.config.not_a_server_error.expected_statuses or [])

    status_code = response.status_code
    if status_code not in expected_statuses:
        raise ServerError(operation=case.operation.label, status_code=status_code)
    if isinstance(case.operation.schema, GraphQLSchema):
        try:
            data = response.json()
            validate_graphql_response(case, data)
        except json.JSONDecodeError as exc:
            raise MalformedJson.from_exception(operation=case.operation.label, exc=exc) from None
    return None


DEFAULT_MAX_RESPONSE_TIME = 10.0


def max_response_time(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    limit = ctx.config.max_response_time.limit or DEFAULT_MAX_RESPONSE_TIME
    elapsed = response.elapsed
    if elapsed > limit:
        raise ResponseTimeExceeded(
            operation=case.operation.label,
            message=f"Actual: {elapsed:.2f}ms\nLimit: {limit * 1000:.2f}ms",
            elapsed=elapsed,
            deadline=limit,
        )
    return None


def run_checks(
    *,
    case: Case,
    response: Response,
    ctx: CheckContext,
    checks: Iterable[CheckFunction],
    on_failure: Callable[[str, set[Failure], Failure], None],
    on_success: Callable[[str, Case], None] | None = None,
) -> set[Failure]:
    """Run a set of checks against a response."""
    collected: set[Failure] = set()

    for check in checks:
        name = check.__name__
        try:
            skip_check = check(ctx, response, case)
            if not skip_check and on_success:
                on_success(name, case)
        except Failure as failure:
            on_failure(name, collected, failure.with_traceback(None))
        except AssertionError as exc:
            custom_failure = CustomFailure(
                operation=case.operation.label,
                title=f"Custom check failed: `{name}`",
                message=str(exc),
                exception=exc,
            )
            on_failure(name, collected, custom_failure)
        except FailureGroup as group:
            for sub_failure in group.exceptions:
                on_failure(name, collected, sub_failure)

    return collected


def __getattr__(name: str) -> Any:
    try:
        return CHECKS.get_one(name)
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + CHECKS.get_all_names())
