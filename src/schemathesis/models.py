from __future__ import annotations

import http.client
import textwrap
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
)

from schemathesis.checks import CHECKS, CheckContext, CheckFunction
from schemathesis.core import NOT_SET, SCHEMATHESIS_TEST_CASE_HEADER, NotSet, curl
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.core.output import prepare_response_payload
from schemathesis.core.transforms import diff
from schemathesis.core.transport import Response
from schemathesis.generation.meta import CaseMetadata, ComponentKind
from schemathesis.transport.prepare import prepare_request

from ._override import CaseOverride
from .generation import generate_random_case_id
from .hooks import HookContext, dispatch

if TYPE_CHECKING:
    import requests.auth
    from requests.structures import CaseInsensitiveDict

    from schemathesis.schemas import APIOperation


@dataclass
class TransitionId:
    name: str
    status_code: str

    __slots__ = ("name", "status_code")


@dataclass
class CaseSource:
    """Data sources, used to generate a test case."""

    case: Case
    response: Response
    elapsed: float
    overrides_all_parameters: bool
    transition_id: TransitionId


@dataclass(repr=False)
class Case:
    """A single test case parameters."""

    operation: APIOperation
    method: str
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
    source: CaseSource | None = None

    meta: CaseMetadata | None = None

    _auth: requests.auth.AuthBase | None = None
    _has_explicit_auth: bool = False

    def __post_init__(self) -> None:
        self._original_path_parameters = self.path_parameters.copy() if self.path_parameters else None
        self._original_headers = self.headers.copy() if self.headers else None
        self._original_cookies = self.cookies.copy() if self.cookies else None
        self._original_query = self.query.copy() if self.query else None

    def _has_generated_component(self, component: ComponentKind) -> bool:
        if self.meta is None:
            return False
        return self.meta.components.get(component) is not None

    def _get_diff(self, component: ComponentKind) -> dict[str, Any]:
        original = getattr(self, f"_original_{component.value}")
        current = getattr(self, component.value)
        if not (current and original):
            return {}
        original_value = original if self._has_generated_component(component) else {}
        return diff(original_value, current)

    @property
    def _override(self) -> CaseOverride:
        return CaseOverride(
            path_parameters=self._get_diff(ComponentKind.PATH_PARAMETERS),
            headers=self._get_diff(ComponentKind.HEADERS),
            query=self._get_diff(ComponentKind.QUERY),
            cookies=self._get_diff(ComponentKind.COOKIES),
        )

    def asdict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "meta": self.meta.asdict() if self.meta is not None else None,
            "verbose_name": self.operation.verbose_name,
            "path_template": self.path,
            "path_parameters": self.path_parameters,
            "query": self.query,
            "cookies": self.cookies,
            "media_type": self.media_type,
        }

    def __repr__(self) -> str:
        parts = [f"{self.__class__.__name__}("]
        first = True
        for name in ("path_parameters", "headers", "cookies", "query", "body"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, NotSet):
                if first:
                    first = False
                else:
                    parts.append(", ")
                parts.extend((name, "=", repr(value)))
        return "".join(parts) + ")"

    def __hash__(self) -> int:
        return hash(self.as_curl_command({SCHEMATHESIS_TEST_CASE_HEADER: "0"}))

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    @property
    def path(self) -> str:
        return self.operation.path

    @property
    def full_path(self) -> str:
        return self.operation.full_path

    @property
    def base_url(self) -> str | None:
        return self.operation.base_url

    @property
    def app(self) -> Any:
        return self.operation.app

    @property
    def formatted_path(self) -> str:
        try:
            return self.path.format(**self.path_parameters or {})
        except KeyError as exc:
            # This may happen when a path template has a placeholder for variable "X", but parameter "X" is not defined
            # in the parameters list.
            # When `exc` is formatted, it is the missing key name in quotes. E.g. 'id'
            raise InvalidSchema(f"Path parameter {exc} is not defined") from exc
        except (IndexError, ValueError) as exc:
            # A single unmatched `}` inside the path template may cause this
            raise InvalidSchema(f"Malformed path template: `{self.path}`\n\n  {exc}") from exc

    def as_curl_command(self, headers: dict[str, Any] | None = None, verify: bool = True) -> str:
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
        if self.app is not None:
            kwargs["app"] = self.app
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

        checks = checks or CHECKS.get_all()
        checks = [check for check in checks if check not in (excluded_checks or [])]
        for check in additional_checks or []:
            if check not in checks and check not in (excluded_checks or []):
                checks.append(check)
        failures: set[Failure] = set()
        ctx = CheckContext(
            override=self._override,
            auth=None,
            headers=CaseInsensitiveDict(headers) if headers else None,
            config={},
            transport_kwargs=transport_kwargs,
        )
        for check in checks:
            try:
                check(ctx, response, self)
            except Failure as f:
                # Tracebacks are not relevant here
                failures.add(f.with_traceback(None))
            except AssertionError as exc:
                failures.add(
                    Failure.from_assertion(
                        name=check.__name__,
                        operation=self.operation.verbose_name,
                        exc=exc,
                    )
                )
        if failures:
            message = f"Schemathesis found {len(failures)} distinct failure"
            if len(failures) > 1:
                message += "s"
            reason = http.client.responses.get(response.status_code, "Unknown")
            message += f".\n\n[{response.status_code}] {reason}:"
            payload = response.text
            if not payload:
                message += "\n\n    <EMPTY>"
            else:
                payload = prepare_response_payload(payload, config=self.operation.schema.output_config)
                payload = textwrap.indent(f"\n`{payload}`", prefix="    ")
                message += f"\n{payload}"
            verify = getattr(response, "verify", True)
            code_sample = self.as_curl_command(headers=dict(response.request.headers), verify=verify)
            message += f"\n\nReproduce with:\n\n    {code_sample}\n\n"
            raise FailureGroup(list(failures), message) from None

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
