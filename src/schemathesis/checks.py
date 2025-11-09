from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator, Optional

from schemathesis.config import ChecksConfig, OutputConfig, SensitiveDataLeakConfig
from schemathesis.core.failures import (
    CustomFailure,
    Failure,
    FailureGroup,
    MalformedJson,
    ResponseTimeExceeded,
    SensitiveDataLeakFailure,
    ServerError,
)
from schemathesis.core.output import prepare_response_payload
from schemathesis.core.registries import Registry
from schemathesis.core.transport import Response
from schemathesis.generation.overrides import Override

if TYPE_CHECKING:
    from requests.models import CaseInsensitiveDict

    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.generation.case import Case

CheckFunction = Callable[["CheckContext", "Response", "Case"], Optional[bool]]

_SENSITIVE_DATA_SNIPPET_LIMIT = 180


class BuiltinMarker:
    __slots__ = ("pattern", "assessment")

    def __init__(self, pattern: re.Pattern[str], assessment: str | None = None) -> None:
        self.pattern = pattern
        self.assessment = assessment


class ResolvedMarker:
    __slots__ = ("marker_id", "pattern", "assessment")

    def __init__(
        self,
        *,
        marker_id: str,
        pattern: re.Pattern[str],
        assessment: str | None,
    ) -> None:
        self.marker_id = marker_id
        self.pattern = pattern
        self.assessment = assessment


_SENSITIVE_DATA_BUILTIN_MARKERS: dict[str, BuiltinMarker] = {
    "sql_statement": BuiltinMarker(
        re.compile(
            r"(?i)(?:SQL:\s+)?"
            r"(?:SELECT|INSERT|UPDATE|DELETE|UPSERT|MERGE|CREATE|DROP|ALTER)\b"
            r"(?:\s+INTO|\s+FROM|\s+TABLE|\s+DATABASE|\s+VIEW)?[^\r\n]*"
        ),
        assessment="Raw SQL was sent to the client; turn off SQL debug logging and scrub DB statements from responses.",
    ),
}


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


@check
def sensitive_data_leak(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    """Detect secrets or debug artefacts in responses."""
    config = ctx.config.sensitive_data_leak
    if not config.enabled:
        return None

    markers = _resolve_sensitive_markers(config)
    if not markers:
        return None

    output_config = case.operation.schema.config.output
    failures: list[Failure] = []
    try:
        body_text = response.text
    except UnicodeDecodeError:
        body_text = None
    if body_text:
        matches = _collect_marker_matches(markers, body_text, output_config)
        for marker, snippet in matches:
            failures.append(
                SensitiveDataLeakFailure(
                    operation=case.operation.label,
                    marker=marker.marker_id,
                    matched=snippet,
                    location="body",
                    assessment=marker.assessment,
                )
            )

    for header_name, values in response.headers.items():
        name_matches = _collect_marker_matches(markers, header_name, output_config)
        for marker, snippet in name_matches:
            failures.append(
                SensitiveDataLeakFailure(
                    operation=case.operation.label,
                    marker=marker.marker_id,
                    matched=snippet,
                    location=f"header name `{header_name}`",
                    assessment=marker.assessment,
                )
            )

        for value in values:
            matches = _collect_marker_matches(markers, value, output_config)
            for marker, snippet in matches:
                failures.append(
                    SensitiveDataLeakFailure(
                        operation=case.operation.label,
                        marker=marker.marker_id,
                        matched=snippet,
                        location=f"header `{header_name}`",
                        assessment=marker.assessment,
                    )
                )

    if not failures:
        return None
    if len(failures) == 1:
        raise failures[0]
    raise FailureGroup(failures)


def _resolve_sensitive_markers(config: SensitiveDataLeakConfig) -> list[ResolvedMarker]:
    resolved: list[ResolvedMarker] = []
    for marker_id in config.builtins:
        builtin = _SENSITIVE_DATA_BUILTIN_MARKERS.get(marker_id)
        if builtin is not None:
            resolved.append(ResolvedMarker(marker_id=marker_id, pattern=builtin.pattern, assessment=builtin.assessment))

    for marker in config.markers:
        resolved.append(ResolvedMarker(marker_id=marker.name, pattern=marker.compile(), assessment=marker.assessment))
    return resolved


def _collect_marker_matches(
    markers: list[ResolvedMarker],
    text: str,
    output_config: OutputConfig | None,
) -> list[tuple[ResolvedMarker, str]]:
    matches: list[tuple[ResolvedMarker, str]] = []
    for marker in markers:
        for match in marker.pattern.finditer(text):
            snippet = _clip_snippet(match.group(0), output_config=output_config)
            if snippet:
                matches.append((marker, snippet))
    return matches


def _clip_snippet(snippet: str, *, output_config: OutputConfig | None) -> str:
    snippet = snippet.strip()
    if not snippet:
        return snippet
    if output_config is not None:
        return prepare_response_payload(snippet, config=output_config)
    if len(snippet) <= _SENSITIVE_DATA_SNIPPET_LIMIT:
        return snippet
    return snippet[: _SENSITIVE_DATA_SNIPPET_LIMIT - 3] + "..."


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
