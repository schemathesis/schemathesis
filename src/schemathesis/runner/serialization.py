"""Transformation from Schemathesis-specific data structures to ones that can be serialized and sent over network.

They all consist of primitive types and don't have references to schemas, app, etc.
"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union, TYPE_CHECKING

from ..transports import serialize_payload
from ..code_samples import get_excluded_headers
from ..exceptions import (
    FailureContext,
    InternalError,
    make_unique_by_key,
    format_exception,
    extract_requests_exception_details,
    RuntimeErrorType,
    DeadlineExceeded,
    OperationSchemaError,
    BodyInGetRequestError,
    InvalidRegularExpression,
)
from ..models import Case, Check, Interaction, Request, Response, Status, TestResult

if TYPE_CHECKING:
    import hypothesis.errors
    from requests.structures import CaseInsensitiveDict


@dataclass
class SerializedCase:
    # Case data
    id: str
    path_parameters: Optional[Dict[str, Any]]
    headers: Optional[Dict[str, Any]]
    cookies: Optional[Dict[str, Any]]
    query: Optional[Dict[str, Any]]
    body: Optional[str]
    media_type: Optional[str]
    data_generation_method: Optional[str]
    # Operation data
    method: str
    url: str
    path_template: str
    verbose_name: str
    # Transport info
    verify: bool
    # Headers coming from sources outside data generation
    extra_headers: Dict[str, Any]

    @classmethod
    def from_case(cls, case: Case, headers: Optional[Dict[str, Any]], verify: bool) -> "SerializedCase":
        # `headers` include not only explicitly provided headers but also ones added by hooks, custom auth, etc.
        request_data = case.prepare_code_sample_data(headers)
        serialized_body = _serialize_body(request_data.body)
        return cls(
            id=case.id,
            path_parameters=case.path_parameters,
            headers=dict(case.headers) if case.headers is not None else None,
            cookies=case.cookies,
            query=case.query,
            body=serialized_body,
            media_type=case.media_type,
            data_generation_method=case.data_generation_method.as_short_name()
            if case.data_generation_method is not None
            else None,
            method=case.method,
            url=request_data.url,
            path_template=case.path,
            verbose_name=case.operation.verbose_name,
            verify=verify,
            extra_headers=request_data.headers,
        )


def _serialize_body(body: Optional[Union[str, bytes]]) -> Optional[str]:
    if body is None:
        return None
    if isinstance(body, str):
        body = body.encode("utf-8")
    return serialize_payload(body)


@dataclass
class SerializedCheck:
    # Check name
    name: str
    # Check result
    value: Status
    request: Request
    response: Optional[Response]
    # Generated example
    example: SerializedCase
    # Message could be absent for plain `assert` statements
    message: Optional[str] = None
    # Failure-specific context
    context: Optional[FailureContext] = None
    # Cases & responses that were made before this one
    history: List["SerializedHistoryEntry"] = field(default_factory=list)

    @classmethod
    def from_check(cls, check: Check) -> "SerializedCheck":
        import requests
        from ..transports.responses import WSGIResponse

        if check.response is not None:
            request = Request.from_prepared_request(check.response.request)
        elif check.request is not None:
            # Response is not available, but it is not an error (only time-out behaves this way at the moment)
            request = Request.from_prepared_request(check.request)
        else:
            raise InternalError("Can not find request data")

        response: Optional[Response]
        if isinstance(check.response, requests.Response):
            response = Response.from_requests(check.response)
        elif isinstance(check.response, WSGIResponse):
            response = Response.from_wsgi(check.response, check.elapsed)
        else:
            response = None
        headers = _get_headers(request.headers)
        history = get_serialized_history(check.example)
        return cls(
            name=check.name,
            value=check.value,
            example=SerializedCase.from_case(
                check.example, headers, verify=response.verify if response is not None else True
            ),
            message=check.message,
            request=request,
            response=response,
            context=check.context,
            history=history,
        )


def _get_headers(headers: Union[Dict[str, Any], CaseInsensitiveDict]) -> Dict[str, str]:
    return {key: value[0] for key, value in headers.items() if key not in get_excluded_headers()}


@dataclass
class SerializedHistoryEntry:
    case: SerializedCase
    response: Response


def get_serialized_history(case: Case) -> List[SerializedHistoryEntry]:
    import requests

    history = []
    while case.source is not None:
        history_request = case.source.response.request
        headers = _get_headers(history_request.headers)
        if isinstance(case.source.response, requests.Response):
            history_response = Response.from_requests(case.source.response)
            verify = history_response.verify
        else:
            history_response = Response.from_wsgi(case.source.response, case.source.elapsed)
            verify = True
        entry = SerializedHistoryEntry(
            case=SerializedCase.from_case(case.source.case, headers, verify=verify), response=history_response
        )
        history.append(entry)
        case = case.source.case
    return history


@dataclass
class SerializedError:
    type: RuntimeErrorType
    title: Optional[str]
    message: Optional[str]
    extras: List[str]

    # Exception info
    exception: str
    exception_with_traceback: str

    @classmethod
    def with_exception(
        cls,
        type_: RuntimeErrorType,
        title: Optional[str],
        message: Optional[str],
        extras: List[str],
        exception: Exception,
    ) -> "SerializedError":
        return cls(
            type=type_,
            title=title,
            message=message,
            extras=extras,
            exception=format_exception(exception),
            exception_with_traceback=format_exception(exception, True),
        )

    @classmethod
    def from_exception(cls, exception: Exception) -> "SerializedError":
        import requests
        import hypothesis.errors
        from hypothesis import HealthCheck

        title = "Runtime Error"
        message: Optional[str]
        if isinstance(exception, requests.RequestException):
            if isinstance(exception, requests.exceptions.SSLError):
                type_ = RuntimeErrorType.CONNECTION_SSL
            elif isinstance(exception, requests.exceptions.ConnectionError):
                type_ = RuntimeErrorType.CONNECTION_OTHER
            else:
                type_ = RuntimeErrorType.NETWORK_OTHER
            message, extras = extract_requests_exception_details(exception)
            title = "Network Error"
        elif isinstance(exception, DeadlineExceeded):
            type_ = RuntimeErrorType.HYPOTHESIS_DEADLINE_EXCEEDED
            message = str(exception).strip()
            extras = []
        elif isinstance(exception, hypothesis.errors.Unsatisfiable):
            type_ = RuntimeErrorType.HYPOTHESIS_UNSATISFIABLE
            message = f"{exception}. Possible reasons:"
            extras = [
                "- Contradictory schema constraints, such as a minimum value exceeding the maximum.",
                "- Excessive schema complexity, which hinders parameter generation.",
            ]
            title = "Schema Error"
        elif isinstance(exception, hypothesis.errors.FailedHealthCheck):
            health_check = _health_check_from_error(exception)
            if health_check is not None:
                message, type_ = {
                    HealthCheck.data_too_large: (
                        HEALTH_CHECK_MESSAGE_DATA_TOO_LARGE,
                        RuntimeErrorType.HYPOTHESIS_HEALTH_CHECK_DATA_TOO_LARGE,
                    ),
                    HealthCheck.filter_too_much: (
                        HEALTH_CHECK_MESSAGE_FILTER_TOO_MUCH,
                        RuntimeErrorType.HYPOTHESIS_HEALTH_CHECK_FILTER_TOO_MUCH,
                    ),
                    HealthCheck.too_slow: (
                        HEALTH_CHECK_MESSAGE_TOO_SLOW,
                        RuntimeErrorType.HYPOTHESIS_HEALTH_CHECK_TOO_SLOW,
                    ),
                    HealthCheck.large_base_example: (
                        HEALTH_CHECK_MESSAGE_LARGE_BASE_EXAMPLE,
                        RuntimeErrorType.HYPOTHESIS_HEALTH_CHECK_LARGE_BASE_EXAMPLE,
                    ),
                }[health_check]
            else:
                type_ = RuntimeErrorType.UNCLASSIFIED
                message = str(exception)
            extras = []
            title = "Failed Health Check"
        elif isinstance(exception, OperationSchemaError):
            if isinstance(exception, BodyInGetRequestError):
                type_ = RuntimeErrorType.SCHEMA_BODY_IN_GET_REQUEST
            elif isinstance(exception, InvalidRegularExpression):
                type_ = RuntimeErrorType.SCHEMA_INVALID_REGULAR_EXPRESSION
            else:
                type_ = RuntimeErrorType.SCHEMA_GENERIC
            message = exception.message
            extras = []
            title = "Schema Error"
        else:
            type_ = RuntimeErrorType.UNCLASSIFIED
            message = str(exception)
            extras = []
        return cls.with_exception(type_=type_, exception=exception, title=title, message=message, extras=extras)


HEALTH_CHECK_MESSAGE_DATA_TOO_LARGE = """There's a notable occurrence of examples surpassing the maximum size limit.
Typically, generating excessively large examples can compromise the quality of test outcomes.

Consider revising the schema to more accurately represent typical use cases
or applying constraints to reduce the data size."""
HEALTH_CHECK_MESSAGE_FILTER_TOO_MUCH = """A significant number of generated examples are being filtered out, indicating
that the schema's constraints may be too complex.

This level of filtration can slow down testing and affect the distribution
of generated data. Review and simplify the schema constraints where
possible to mitigate this issue."""
HEALTH_CHECK_MESSAGE_TOO_SLOW = "Data generation is extremely slow. Consider reducing the complexity of the schema."
HEALTH_CHECK_MESSAGE_LARGE_BASE_EXAMPLE = """A health check has identified that the smallest example derived from the schema
is excessively large, potentially leading to inefficient test execution.

This is commonly due to schemas that specify large-scale data structures by
default, such as an array with an extensive number of elements.

Consider revising the schema to more accurately represent typical use cases
or applying constraints to reduce the data size."""


def _health_check_from_error(exception: hypothesis.errors.FailedHealthCheck) -> Optional[hypothesis.HealthCheck]:
    from hypothesis import HealthCheck

    match = re.search(r"add HealthCheck\.(\w+) to the suppress_health_check ", str(exception))
    if match:
        return {
            "data_too_large": HealthCheck.data_too_large,
            "filter_too_much": HealthCheck.filter_too_much,
            "too_slow": HealthCheck.too_slow,
            "large_base_example": HealthCheck.large_base_example,
        }.get(match.group(1))
    return None


@dataclass
class SerializedInteraction:
    request: Request
    response: Response
    checks: List[SerializedCheck]
    status: Status
    recorded_at: str

    @classmethod
    def from_interaction(cls, interaction: Interaction) -> "SerializedInteraction":
        return cls(
            request=interaction.request,
            response=interaction.response,
            checks=[SerializedCheck.from_check(check) for check in interaction.checks],
            status=interaction.status,
            recorded_at=interaction.recorded_at,
        )


@dataclass
class SerializedTestResult:
    method: str
    path: str
    verbose_name: str
    has_failures: bool
    has_errors: bool
    has_logs: bool
    is_errored: bool
    is_flaky: bool
    is_skipped: bool
    seed: Optional[int]
    data_generation_method: List[str]
    checks: List[SerializedCheck]
    logs: List[str]
    errors: List[SerializedError]
    interactions: List[SerializedInteraction]

    @classmethod
    def from_test_result(cls, result: TestResult) -> "SerializedTestResult":
        formatter = logging.Formatter("[%(asctime)s] %(levelname)s in %(module)s: %(message)s")
        return cls(
            method=result.method,
            path=result.path,
            verbose_name=result.verbose_name,
            has_failures=result.has_failures,
            has_errors=result.has_errors,
            has_logs=result.has_logs,
            is_errored=result.is_errored,
            is_flaky=result.is_flaky,
            is_skipped=result.is_skipped,
            seed=result.seed,
            data_generation_method=[m.as_short_name() for m in result.data_generation_method],
            checks=[SerializedCheck.from_check(check) for check in result.checks],
            logs=[formatter.format(record) for record in result.logs],
            errors=[SerializedError.from_exception(error) for error in result.errors],
            interactions=[SerializedInteraction.from_interaction(interaction) for interaction in result.interactions],
        )


def deduplicate_failures(checks: List[SerializedCheck]) -> List[SerializedCheck]:
    """Return only unique checks that should be displayed in the output."""
    seen: Set[Tuple[Optional[str], ...]] = set()
    unique_checks = []
    for check in reversed(checks):
        # There are also could be checks that didn't fail
        if check.value == Status.failure:
            key = make_unique_by_key(check.name, check.message, check.context)
            if key not in seen:
                unique_checks.append(check)
                seen.add(key)
    return unique_checks
