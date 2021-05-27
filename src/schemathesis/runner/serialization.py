"""Transformation from Schemathesis-specific data structures to ones that can be serialized and sent over network.

They all consist of primitive types and don't have references to schemas, app, etc.
"""
import logging
from typing import Any, Dict, List, Optional

import attr
import requests

from ..exceptions import FailureContext, InternalError
from ..models import Case, Check, Interaction, Request, Response, Status, TestResult
from ..utils import WSGIResponse, format_exception


@attr.s(slots=True)  # pragma: no mutate
class SerializedCase:
    text_lines: List[str] = attr.ib()  # pragma: no mutate
    requests_code: str = attr.ib()
    curl_code: str = attr.ib()
    path_template: str = attr.ib()
    path_parameters: Optional[Dict[str, Any]] = attr.ib()
    query: Optional[Dict[str, Any]] = attr.ib()
    verbose_name: str = attr.ib()
    media_type: Optional[str] = attr.ib()

    @classmethod
    def from_case(cls, case: Case, headers: Optional[Dict[str, Any]]) -> "SerializedCase":
        return cls(
            text_lines=case.as_text_lines(headers),
            requests_code=case.get_code_to_reproduce(headers),
            curl_code=case.as_curl_command(headers),
            path_template=case.full_path,
            path_parameters=case.path_parameters,
            query=case.query,
            verbose_name=case.operation.verbose_name,
            media_type=case.media_type,
        )


@attr.s(slots=True)  # pragma: no mutate
class SerializedCheck:
    # Check name
    name: str = attr.ib()  # pragma: no mutate
    # Check result
    value: Status = attr.ib()  # pragma: no mutate
    request: Request = attr.ib()  # pragma: no mutate
    response: Optional[Response] = attr.ib()  # pragma: no mutate
    # Generated example
    example: SerializedCase = attr.ib()  # pragma: no mutate
    message: Optional[str] = attr.ib(default=None)  # pragma: no mutate
    # Failure-specific context
    context: Optional[FailureContext] = attr.ib(default=None)  # pragma: no mutate

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
        headers = {key: value[0] for key, value in request.headers.items()}
        return SerializedCheck(
            name=check.name,
            value=check.value,
            example=SerializedCase.from_case(check.example, headers),
            message=check.message,
            request=request,
            response=response,
            context=check.context,
        )


@attr.s(slots=True)  # pragma: no mutate
class SerializedError:
    exception: str = attr.ib()  # pragma: no mutate
    exception_with_traceback: str = attr.ib()  # pragma: no mutate
    example: Optional[SerializedCase] = attr.ib()  # pragma: no mutate
    title: Optional[str] = attr.ib()  # pragma: no mutate

    @classmethod
    def from_error(
        cls, exception: Exception, case: Optional[Case], headers: Optional[Dict[str, Any]], title: Optional[str] = None
    ) -> "SerializedError":
        return cls(
            exception=format_exception(exception),
            exception_with_traceback=format_exception(exception, True),
            example=SerializedCase.from_case(case, headers) if case else None,
            title=title,
        )


@attr.s(slots=True)  # pragma: no mutate
class SerializedInteraction:
    request: Request = attr.ib()  # pragma: no mutate
    response: Response = attr.ib()  # pragma: no mutate
    checks: List[SerializedCheck] = attr.ib()  # pragma: no mutate
    status: Status = attr.ib()  # pragma: no mutate
    recorded_at: str = attr.ib()  # pragma: no mutate

    @classmethod
    def from_interaction(cls, interaction: Interaction) -> "SerializedInteraction":
        return cls(
            request=interaction.request,
            response=interaction.response,
            checks=[SerializedCheck.from_check(check) for check in interaction.checks],
            status=interaction.status,
            recorded_at=interaction.recorded_at,
        )


@attr.s(slots=True)  # pragma: no mutate
class SerializedTestResult:
    method: str = attr.ib()  # pragma: no mutate
    path: str = attr.ib()  # pragma: no mutate
    has_failures: bool = attr.ib()  # pragma: no mutate
    has_errors: bool = attr.ib()  # pragma: no mutate
    has_logs: bool = attr.ib()  # pragma: no mutate
    is_errored: bool = attr.ib()  # pragma: no mutate
    seed: Optional[int] = attr.ib()  # pragma: no mutate
    data_generation_method: str = attr.ib()  # pragma: no mutate
    checks: List[SerializedCheck] = attr.ib()  # pragma: no mutate
    logs: List[str] = attr.ib()  # pragma: no mutate
    errors: List[SerializedError] = attr.ib()  # pragma: no mutate
    interactions: List[SerializedInteraction] = attr.ib()  # pragma: no mutate

    @classmethod
    def from_test_result(cls, result: TestResult) -> "SerializedTestResult":
        formatter = logging.Formatter("[%(asctime)s] %(levelname)s in %(module)s: %(message)s")
        return SerializedTestResult(
            method=result.method,
            path=result.path,
            has_failures=result.has_failures,
            has_errors=result.has_errors,
            has_logs=result.has_logs,
            is_errored=result.is_errored,
            seed=result.seed,
            data_generation_method=result.data_generation_method.as_short_name(),
            checks=[SerializedCheck.from_check(check) for check in result.checks],
            logs=[formatter.format(record) for record in result.logs],
            errors=[SerializedError.from_error(*error, headers=result.overridden_headers) for error in result.errors],
            interactions=[SerializedInteraction.from_interaction(interaction) for interaction in result.interactions],
        )
