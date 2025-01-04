from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from schemathesis.checks import CHECKS, CheckContext, CheckFunction, run_checks
from schemathesis.core import NOT_SET, SCHEMATHESIS_TEST_CASE_HEADER, NotSet, curl
from schemathesis.core.failures import FailureGroup, failure_report_title, format_failures
from schemathesis.core.transport import Response
from schemathesis.generation import generate_random_case_id
from schemathesis.generation.meta import CaseMetadata
from schemathesis.generation.overrides import Override, store_components
from schemathesis.hooks import HookContext, dispatch
from schemathesis.transport.prepare import prepare_request

if TYPE_CHECKING:
    import requests.auth
    from requests.structures import CaseInsensitiveDict

    from schemathesis.schemas import APIOperation


@dataclass
class Case:
    """A single test case parameters."""

    operation: APIOperation
    method: str
    path: str
    # Unique test case identifier
    id: str = field(default_factory=generate_random_case_id, compare=False)
    path_parameters: dict[str, Any] | None = None
    headers: CaseInsensitiveDict | None = None
    cookies: dict[str, Any] | None = None
    query: dict[str, Any] | None = None
    # By default, there is no body, but we can't use `None` as the default value because it clashes with `null`
    # which is a valid payload.
    body: list | dict[str, Any] | str | int | float | bool | bytes | NotSet = NOT_SET
    # The media type for cases with a payload. For example, "application/json"
    media_type: str | None = None

    meta: CaseMetadata | None = field(compare=False, default=None)

    _auth: requests.auth.AuthBase | None = None
    _has_explicit_auth: bool = False

    def __post_init__(self) -> None:
        self._components = store_components(self)

    @property
    def _override(self) -> Override:
        return Override.from_components(self._components, self)

    def __repr__(self) -> str:
        output = f"{self.__class__.__name__}("
        first = True
        for name in ("path_parameters", "headers", "cookies", "query", "body"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, NotSet):
                if first:
                    first = False
                else:
                    output += ", "
                output += f"{name}={value!r}"
        return f"{output})"

    def __hash__(self) -> int:
        return hash(self.as_curl_command({SCHEMATHESIS_TEST_CASE_HEADER: "0"}))

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    def as_curl_command(self, headers: Mapping[str, Any] | None = None, verify: bool = True) -> str:
        """Construct a curl command for a given case."""
        request_data = prepare_request(self, headers, self.operation.schema.output_config.sanitize)
        return curl.generate(
            method=str(request_data.method),
            url=str(request_data.url),
            body=request_data.body,
            verify=verify,
            headers=dict(request_data.headers),
            known_generated_headers=dict(self.headers or {}),
        )

    def as_transport_kwargs(self, base_url: str | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Convert the test case into a dictionary acceptable by the underlying transport call."""
        return self.operation.schema.transport.serialize_case(self, base_url=base_url, headers=headers)

    def call(
        self,
        base_url: str | None = None,
        session: requests.Session | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Response:
        hook_context = HookContext(operation=self.operation)
        dispatch("before_call", hook_context, self, **kwargs)
        if self.operation.app is not None:
            kwargs["app"] = self.operation.app
        response = self.operation.schema.transport.send(
            self,
            session=session,
            base_url=base_url,
            headers=headers,
            params=params,
            cookies=cookies,
            **kwargs,
        )
        dispatch("after_call", hook_context, self, response)
        return response

    def validate_response(
        self,
        response: Response,
        checks: list[CheckFunction] | None = None,
        additional_checks: list[CheckFunction] | None = None,
        excluded_checks: list[CheckFunction] | None = None,
        headers: dict[str, Any] | None = None,
        transport_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Validate application response.

        By default, all available checks will be applied.

        :param response: Application response.
        :param checks: A tuple of check functions that accept ``response`` and ``case``.
        :param additional_checks: A tuple of additional checks that will be executed after ones from the ``checks``
            argument.
        :param excluded_checks: Checks excluded from the default ones.
        """
        __tracebackhide__ = True
        from requests.structures import CaseInsensitiveDict

        checks = [
            check
            for check in list(checks or CHECKS.get_all()) + list(additional_checks or [])
            if check not in set(excluded_checks or [])
        ]

        ctx = CheckContext(
            override=self._override,
            auth=None,
            headers=CaseInsensitiveDict(headers) if headers else None,
            config={},
            transport_kwargs=transport_kwargs,
            recorder=None,
        )
        failures = run_checks(
            case=self,
            response=response,
            ctx=ctx,
            checks=checks,
            on_failure=lambda _, collected, failure: collected.add(failure),
        )
        if failures:
            _failures = list(failures)
            message = failure_report_title(_failures) + "\n"
            verify = getattr(response, "verify", True)
            curl = self.as_curl_command(headers=dict(response.request.headers), verify=verify)
            message += format_failures(
                case_id=None,
                response=response,
                failures=_failures,
                curl=curl,
                config=self.operation.schema.output_config,
            )
            raise FailureGroup(_failures, message) from None

    def call_and_validate(
        self,
        base_url: str | None = None,
        session: requests.Session | None = None,
        headers: dict[str, Any] | None = None,
        checks: list[CheckFunction] | None = None,
        additional_checks: list[CheckFunction] | None = None,
        excluded_checks: list[CheckFunction] | None = None,
        **kwargs: Any,
    ) -> Response:
        __tracebackhide__ = True
        response = self.call(base_url, session, headers, **kwargs)
        self.validate_response(
            response,
            checks,
            headers=headers,
            additional_checks=additional_checks,
            excluded_checks=excluded_checks,
            transport_kwargs=kwargs,
        )
        return response
