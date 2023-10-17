"""Transformation from Schemathesis-specific data structures to ones that can be serialized and sent over network.

They all consist of primitive types and don't have references to schemas, app, etc.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import requests
from requests.structures import CaseInsensitiveDict

from ..code_samples import EXCLUDED_HEADERS
from ..exceptions import FailureContext, InternalError, make_unique_by_key
from ..models import Case, Check, Interaction, Request, Response, Status, TestResult, serialize_payload
from ..utils import WSGIResponse, format_exception


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
    message: Optional[str] = None
    # Failure-specific context
    context: Optional[FailureContext] = None
    # Cases & responses that were made before this one
    history: List["SerializedHistoryEntry"] = field(default_factory=list)

    @classmethod
    def from_check(cls, check: Check) -> "SerializedCheck":
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
    return {key: value[0] for key, value in headers.items() if key not in EXCLUDED_HEADERS}


@dataclass
class SerializedHistoryEntry:
    case: SerializedCase
    response: Response


def get_serialized_history(case: Case) -> List[SerializedHistoryEntry]:
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
    exception: str
    exception_with_traceback: str
    title: Optional[str]

    @classmethod
    def from_error(
        cls,
        exception: Exception,
        title: Optional[str] = None,
    ) -> "SerializedError":
        return cls(
            exception=format_exception(exception),
            exception_with_traceback=format_exception(exception, True),
            title=title,
        )


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
            errors=[SerializedError.from_error(error) for error in result.errors],
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
