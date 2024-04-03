from __future__ import annotations
import datetime
import inspect
import textwrap
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from functools import partial, lru_cache
from itertools import chain
from logging import LogRecord
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Generic,
    Iterator,
    NoReturn,
    Optional,
    Sequence,
    Type,
    TypeVar,
    cast,
)
from urllib.parse import urlsplit, urlunsplit, unquote, urljoin, quote

from schemathesis.transports import RequestsTransport

from . import serializers
from .auths import AuthStorage
from .code_samples import CodeSampleStyle
from .generation import DataGenerationMethod, GenerationConfig
from .constants import (
    SCHEMATHESIS_TEST_CASE_HEADER,
    SERIALIZERS_SUGGESTION_MESSAGE,
    USER_AGENT,
    NOT_SET,
)
from .exceptions import (
    maybe_set_assertion_message,
    CheckFailed,
    FailureContext,
    OperationSchemaError,
    SerializationNotPossible,
    deduplicate_failed_checks,
    get_grouped_exception,
    prepare_response_payload,
    SkipTest,
)
from .internal.deprecation import deprecated_property
from .internal.copy import fast_deepcopy
from .hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher, dispatch
from .parameters import Parameter, ParameterSet, PayloadAlternatives
from .sanitization import sanitize_request, sanitize_response
from .serializers import Serializer
from .types import Body, Cookies, FormData, Headers, NotSet, PathParameters, Query
from .generation import generate_random_case_id

if TYPE_CHECKING:
    import unittest
    from requests.structures import CaseInsensitiveDict
    from hypothesis import strategies as st
    import requests.auth
    from .transports.responses import WSGIResponse
    from .transports import Response, Request
    from .schemas import BaseSchema
    from .stateful import Stateful, StatefulTest


@dataclass
class CaseSource:
    """Data sources, used to generate a test case."""

    case: Case
    response: Response
    elapsed: float

    def partial_deepcopy(self) -> CaseSource:
        return self.__class__(case=self.case.partial_deepcopy(), response=self.response, elapsed=self.elapsed)


def cant_serialize(media_type: str) -> NoReturn:  # type: ignore
    """Reject the current example if we don't know how to send this data to the application."""
    from hypothesis import note, event, reject

    event_text = f"Can't serialize data to `{media_type}`."
    note(f"{event_text} {SERIALIZERS_SUGGESTION_MESSAGE}")
    event(event_text)
    reject()  # type: ignore


@lru_cache
def get_request_signature() -> inspect.Signature:
    import requests

    return inspect.signature(requests.Request)


@dataclass()
class PreparedRequestData:
    method: str
    url: str
    body: str | bytes | None
    headers: Headers


def prepare_request_data(kwargs: dict[str, Any]) -> PreparedRequestData:
    """Prepare request data for generating code samples."""
    import requests

    kwargs = {key: value for key, value in kwargs.items() if key in get_request_signature().parameters}
    request = requests.Request(**kwargs).prepare()
    return PreparedRequestData(
        method=str(request.method), url=str(request.url), body=request.body, headers=dict(request.headers)
    )


@dataclass(repr=False)
class Case:
    """A single test case parameters."""

    operation: APIOperation
    # Time spent on generation of this test case
    generation_time: float
    # Unique test case identifier
    id: str = field(default_factory=generate_random_case_id, compare=False)
    path_parameters: PathParameters | None = None
    headers: CaseInsensitiveDict | None = None
    cookies: Cookies | None = None
    query: Query | None = None
    # By default, there is no body, but we can't use `None` as the default value because it clashes with `null`
    # which is a valid payload.
    body: Body | NotSet = NOT_SET
    # The media type for cases with a payload. For example, "application/json"
    media_type: str | None = None
    source: CaseSource | None = None

    # The way the case was generated (None for manually crafted ones)
    data_generation_method: DataGenerationMethod | None = None
    _auth: requests.auth.AuthBase | None = None

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

    @deprecated_property(removed_in="4.0", replacement="operation")
    def endpoint(self) -> APIOperation:
        return self.operation

    @property
    def path(self) -> str:
        return self.operation.path

    @property
    def full_path(self) -> str:
        return self.operation.full_path

    @property
    def method(self) -> str:
        return self.operation.method.upper()

    @property
    def base_url(self) -> str | None:
        return self.operation.base_url

    @property
    def app(self) -> Any:
        return self.operation.app

    def set_source(self, response: Response, case: Case, elapsed: float) -> None:
        self.source = CaseSource(case=case, response=response, elapsed=elapsed)

    @property
    def formatted_path(self) -> str:
        try:
            return self.path.format(**self.path_parameters or {})
        except KeyError as exc:
            # This may happen when a path template has a placeholder for variable "X", but parameter "X" is not defined
            # in the parameters list.
            # When `exc` is formatted, it is the missing key name in quotes. E.g. 'id'
            raise OperationSchemaError(f"Path parameter {exc} is not defined") from exc
        except (IndexError, ValueError) as exc:
            # A single unmatched `}` inside the path template may cause this
            raise OperationSchemaError(f"Malformed path template: `{self.path}`\n\n  {exc}") from exc

    def get_full_base_url(self) -> str | None:
        """Create a full base url, adding "localhost" for WSGI apps."""
        parts = urlsplit(self.base_url)
        if not parts.hostname:
            path = cast(str, parts.path or "")
            return urlunsplit(("http", "localhost", path or "", "", ""))
        return self.base_url

    def prepare_code_sample_data(self, headers: dict[str, Any] | None) -> PreparedRequestData:
        base_url = self.get_full_base_url()
        kwargs = RequestsTransport().serialize_case(self, base_url=base_url, headers=headers)
        return prepare_request_data(kwargs)

    def get_code_to_reproduce(
        self,
        headers: dict[str, Any] | None = None,
        request: requests.PreparedRequest | None = None,
        verify: bool = True,
    ) -> str:
        """Construct a Python code to reproduce this case with `requests`."""
        if request is not None:
            request_data = prepare_request_data(
                {
                    "method": request.method,
                    "url": request.url,
                    "headers": request.headers,
                    "data": request.body,
                }
            )
        else:
            request_data = self.prepare_code_sample_data(headers)
        return CodeSampleStyle.python.generate(
            method=request_data.method,
            url=request_data.url,
            body=request_data.body,
            headers=dict(self.headers) if self.headers is not None else None,
            verify=verify,
            extra_headers=request_data.headers,
        )

    def as_curl_command(self, headers: dict[str, Any] | None = None, verify: bool = True) -> str:
        """Construct a curl command for a given case."""
        request_data = self.prepare_code_sample_data(headers)
        return CodeSampleStyle.curl.generate(
            method=request_data.method,
            url=request_data.url,
            body=request_data.body,
            headers=dict(self.headers) if self.headers is not None else None,
            verify=verify,
            extra_headers=request_data.headers,
        )

    def build_base_url(self, base_url: str | None = None) -> str:
        if base_url is None:
            if self.base_url is not None:
                base_url = self.base_url
            else:
                raise ValueError(
                    "Base URL is required as `base_url` argument in `call` or should be specified "
                    "in the schema constructor as a part of Schema URL."
                )
        return base_url

    def build_headers(self, headers: dict[str, str] | None = None) -> CaseInsensitiveDict:
        from requests.structures import CaseInsensitiveDict

        final_headers = self.headers.copy() if self.headers is not None else CaseInsensitiveDict()
        if headers:
            final_headers.update(headers)
        final_headers.setdefault("User-Agent", USER_AGENT)
        final_headers.setdefault(SCHEMATHESIS_TEST_CASE_HEADER, self.id)
        return final_headers

    def get_serializer(self) -> Serializer | None:
        """Get a serializer for the payload, if there is any."""
        if self.media_type is not None:
            media_type = serializers.get_first_matching_media_type(self.media_type)
            if media_type is None:
                # This media type is set manually. Otherwise, it should have been rejected during the data generation
                raise SerializationNotPossible.for_media_type(self.media_type)
            # SAFETY: It is safe to assume that serializer will be found, because `media_type` returned above
            # is registered. This intentionally ignores cases with concurrent serializers registry modification.
            cls = cast(Type[serializers.Serializer], serializers.get(media_type))
            return cls()
        return None

    def get_url(self, base_url: str | None) -> str:
        # TODO - duplicate of get_full_url???
        base_url = self.build_base_url(base_url)
        formatted_path = self.formatted_path.lstrip("/")
        if not base_url.endswith("/"):
            base_url += "/"
        return unquote(urljoin(base_url, quote(formatted_path)))

    @property
    def has_non_empty_body(self) -> bool:
        return isinstance(self.body, NotSet)

    def get_requests_auth(self) -> requests.auth.AuthBase | None:
        return self._auth

    def as_transport_kwargs(self, base_url: str | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        return self.operation.schema.transport.serialize_case(self, base_url=base_url, headers=headers)

    def as_requests_kwargs(self, base_url: str | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Convert the case into a dictionary acceptable by requests."""
        return self.operation.schema.transport.serialize_case(self, base_url=base_url, headers=headers)

    def call(
        self,
        base_url: str | None = None,
        # TODO: Support different session types
        session: requests.Session | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Response:
        hook_context = HookContext(operation=self.operation)
        dispatch("before_call", hook_context, self)
        response = self.operation.schema.transport.send(
            self, session=session, base_url=base_url, headers=headers, params=params, cookies=cookies, **kwargs
        )
        dispatch("after_call", hook_context, self, response)
        return response

    def as_werkzeug_kwargs(self, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Convert the case into a dictionary acceptable by werkzeug.Client."""
        return self.operation.schema.transport.serialize_case(self, headers=headers)

    def call_wsgi(
        self,
        app: Any = None,
        headers: dict[str, str] | None = None,
        query_string: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Response:
        hook_context = HookContext(operation=self.operation)
        dispatch("before_call", hook_context, self)
        response = self.operation.schema.transport.send(
            self, session=app, headers=headers, params=query_string, **kwargs
        )
        dispatch("after_call", hook_context, self, response)
        return response

    def call_asgi(
        self,
        app: Any = None,
        base_url: str | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Response:
        hook_context = HookContext(operation=self.operation)
        dispatch("before_call", hook_context, self)
        response = self.operation.schema.transport.send(
            self, session=app, base_url=base_url, headers=headers, params=params, cookies=cookies, **kwargs
        )
        dispatch("after_call", hook_context, self, response)
        return response

    def validate_response(
        self,
        response: Response,
        checks: tuple[CheckFunction, ...] = (),
        additional_checks: tuple[CheckFunction, ...] = (),
        excluded_checks: tuple[CheckFunction, ...] = (),
        code_sample_style: str | None = None,
    ) -> None:
        """Validate application response.

        By default, all available checks will be applied.

        :param response: Application response.
        :param checks: A tuple of check functions that accept ``response`` and ``case``.
        :param additional_checks: A tuple of additional checks that will be executed after ones from the ``checks``
            argument.
        :param excluded_checks: Checks excluded from the default ones.
        :param code_sample_style: Controls the style of code samples for failure reproduction.
        """
        __tracebackhide__ = True
        from .checks import ALL_CHECKS
        from .transports.responses import get_reason

        checks = checks or ALL_CHECKS
        checks = tuple(check for check in checks if check not in excluded_checks)
        additional_checks = tuple(check for check in additional_checks if check not in excluded_checks)
        failed_checks = []
        for check in chain(checks, additional_checks):
            copied_case = self.partial_deepcopy()
            try:
                check(response, copied_case)
            except AssertionError as exc:
                maybe_set_assertion_message(exc, check.__name__)
                failed_checks.append(exc)
        failed_checks = list(deduplicate_failed_checks(failed_checks))
        if failed_checks:
            exception_cls = get_grouped_exception(self.operation.verbose_name, *failed_checks)
            formatted = ""
            for idx, failed in enumerate(failed_checks, 1):
                if isinstance(failed, CheckFailed) and failed.context is not None:
                    title = failed.context.title
                    if failed.context.message:
                        message = failed.context.message
                    else:
                        message = None
                else:
                    title, message = failed.args
                formatted += "\n\n"
                formatted += f"{idx}. {title}"
                if message is not None:
                    formatted += "\n\n"
                    formatted += textwrap.indent(message, prefix="    ")

            status_code = response.status_code
            reason = get_reason(status_code)
            formatted += f"\n\n[{response.status_code}] {reason}:"
            payload = response.body.decode("utf-8") if response.body is not None else None
            if not payload:
                formatted += "\n\n    <EMPTY>"
            else:
                payload = prepare_response_payload(payload)
                payload = textwrap.indent(f"\n`{payload}`", prefix="    ")
                formatted += f"\n{payload}"
            code_sample_style = (
                CodeSampleStyle.from_str(code_sample_style)
                if code_sample_style is not None
                else self.operation.schema.code_sample_style
            )
            verify = getattr(response, "verify", True)
            if self.operation.schema.sanitize_output:
                sanitize_request(response.request)
                sanitize_response(response)
            code_message = self._get_code_message(code_sample_style, response.request, verify=verify)
            raise exception_cls(
                f"{formatted}\n\n" f"{code_message}",
                causes=tuple(failed_checks),
            )

    def _get_code_message(
        self, code_sample_style: CodeSampleStyle, request: requests.PreparedRequest, verify: bool
    ) -> str:
        if code_sample_style == CodeSampleStyle.python:
            code = self.get_code_to_reproduce(request=request, verify=verify)
        elif code_sample_style == CodeSampleStyle.curl:
            code = self.as_curl_command(headers=dict(request.headers), verify=verify)
        else:
            raise ValueError(f"Unknown code sample style: {code_sample_style.name}")
        return f"Reproduce with: \n\n    {code}\n"

    def call_and_validate(
        self,
        base_url: str | None = None,
        session: requests.Session | None = None,
        headers: dict[str, Any] | None = None,
        checks: tuple[CheckFunction, ...] = (),
        code_sample_style: str | None = None,
        **kwargs: Any,
    ) -> Response:
        __tracebackhide__ = True
        response = self.call(base_url, session, headers, **kwargs)
        self.validate_response(response, checks, code_sample_style=code_sample_style)
        return response

    def get_full_url(self) -> str:
        """Make a full URL to the current API operation, including query parameters."""
        import requests

        base_url = self.base_url or "http://127.0.0.1"
        kwargs = self.as_requests_kwargs(base_url)
        request = requests.Request(**kwargs)
        prepared = requests.Session().prepare_request(request)  # type: ignore
        return cast(str, prepared.url)

    def partial_deepcopy(self) -> Case:
        return self.__class__(
            operation=self.operation.partial_deepcopy(),
            data_generation_method=self.data_generation_method,
            media_type=self.media_type,
            source=self.source if self.source is None else self.source.partial_deepcopy(),
            path_parameters=fast_deepcopy(self.path_parameters),
            headers=fast_deepcopy(self.headers),
            cookies=fast_deepcopy(self.cookies),
            query=fast_deepcopy(self.query),
            body=fast_deepcopy(self.body),
            generation_time=self.generation_time,
        )


P = TypeVar("P", bound=Parameter)
D = TypeVar("D", bound=dict)


@dataclass
class OperationDefinition(Generic[P, D]):
    """A wrapper to store not resolved API operation definitions.

    To prevent recursion errors we need to store definitions without resolving references. But operation definitions
    itself can be behind a reference (when there is a ``$ref`` in ``paths`` values), therefore we need to store this
    scope change to have a proper reference resolving later.
    """

    raw: D
    resolved: D
    scope: str
    parameters: Sequence[P]

    def __contains__(self, item: str | int) -> bool:
        return item in self.resolved

    def __getitem__(self, item: str | int) -> None | bool | float | str | list | dict[str, Any]:
        return self.resolved[item]

    def get(self, item: str | int, default: Any = None) -> None | bool | float | str | list | dict[str, Any]:
        return self.resolved.get(item, default)


C = TypeVar("C", bound=Case)


@dataclass(eq=False)
class APIOperation(Generic[P, C]):
    """A single operation defined in an API.

    You can get one via a ``schema`` instance.

    .. code-block:: python

        # Get the POST /items operation
        operation = schema["/items"]["POST"]

    """

    # `path` does not contain `basePath`
    # Example <scheme>://<host>/<basePath>/users - "/users" is path
    # https://swagger.io/docs/specification/2-0/api-host-and-base-path/
    path: str
    method: str
    definition: OperationDefinition = field(repr=False)
    schema: BaseSchema
    verbose_name: str = None  # type: ignore
    app: Any = None
    base_url: str | None = None
    path_parameters: ParameterSet[P] = field(default_factory=ParameterSet)
    headers: ParameterSet[P] = field(default_factory=ParameterSet)
    cookies: ParameterSet[P] = field(default_factory=ParameterSet)
    query: ParameterSet[P] = field(default_factory=ParameterSet)
    body: PayloadAlternatives[P] = field(default_factory=PayloadAlternatives)
    case_cls: type[C] = Case  # type: ignore

    def __post_init__(self) -> None:
        if self.verbose_name is None:
            self.verbose_name = f"{self.method.upper()} {self.full_path}"  # type: ignore

    @property
    def full_path(self) -> str:
        return self.schema.get_full_path(self.path)

    @property
    def links(self) -> dict[str, dict[str, Any]]:
        return self.schema.get_links(self)

    @property
    def tags(self) -> list[str] | None:
        return self.schema.get_tags(self)

    def iter_parameters(self) -> Iterator[P]:
        """Iterate over all operation's parameters."""
        return chain(self.path_parameters, self.headers, self.cookies, self.query)

    def _lookup_container(self, location: str) -> ParameterSet[P] | PayloadAlternatives[P] | None:
        return {
            "path": self.path_parameters,
            "header": self.headers,
            "cookie": self.cookies,
            "query": self.query,
            "body": self.body,
        }.get(location)

    def add_parameter(self, parameter: P) -> None:
        """Add a new processed parameter to an API operation.

        :param parameter: A parameter that will be used with this operation.
        :rtype: None
        """
        # If the parameter has a typo, then by default, there will be an error from `jsonschema` earlier.
        # But if the user wants to skip schema validation, we choose to ignore a malformed parameter.
        # In this case, we still might generate some tests for an API operation, but without this parameter,
        # which is better than skip the whole operation from testing.
        container = self._lookup_container(parameter.location)
        if container is not None:
            container.add(parameter)

    def get_parameter(self, name: str, location: str) -> P | None:
        container = self._lookup_container(location)
        if container is not None:
            return container.get(name)
        return None

    def as_strategy(
        self,
        hooks: HookDispatcher | None = None,
        auth_storage: AuthStorage | None = None,
        data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
        generation_config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> st.SearchStrategy:
        """Turn this API operation into a Hypothesis strategy."""
        strategy = self.schema.get_case_strategy(
            self, hooks, auth_storage, data_generation_method, generation_config=generation_config, **kwargs
        )

        def _apply_hooks(dispatcher: HookDispatcher, _strategy: st.SearchStrategy[Case]) -> st.SearchStrategy[Case]:
            context = HookContext(self)
            for hook in dispatcher.get_all_by_name("before_generate_case"):
                _strategy = hook(context, _strategy)
            for hook in dispatcher.get_all_by_name("filter_case"):
                hook = partial(hook, context)
                _strategy = _strategy.filter(hook)
            for hook in dispatcher.get_all_by_name("map_case"):
                hook = partial(hook, context)
                _strategy = _strategy.map(hook)
            for hook in dispatcher.get_all_by_name("flatmap_case"):
                hook = partial(hook, context)
                _strategy = _strategy.flatmap(hook)
            return _strategy

        strategy = _apply_hooks(GLOBAL_HOOK_DISPATCHER, strategy)
        strategy = _apply_hooks(self.schema.hooks, strategy)
        if hooks is not None:
            strategy = _apply_hooks(hooks, strategy)
        return strategy

    def get_security_requirements(self) -> list[str]:
        return self.schema.get_security_requirements(self)

    def get_strategies_from_examples(self) -> list[st.SearchStrategy[Case]]:
        """Get examples from the API operation."""
        return self.schema.get_strategies_from_examples(self)

    def get_stateful_tests(self, response: Response, stateful: Stateful | None) -> Sequence[StatefulTest]:
        return self.schema.get_stateful_tests(response, self, stateful)

    def get_parameter_serializer(self, location: str) -> Callable | None:
        """Get a function that serializes parameters for the given location.

        It handles serializing data into various `collectionFormat` options and similar.
        Note that payload is handled by this function - it is handled by serializers.
        """
        return self.schema.get_parameter_serializer(self, location)

    def prepare_multipart(self, form_data: FormData) -> tuple[list | None, dict[str, Any] | None]:
        return self.schema.prepare_multipart(form_data, self)

    def get_request_payload_content_types(self) -> list[str]:
        return self.schema.get_request_payload_content_types(self)

    def partial_deepcopy(self) -> APIOperation:
        return self.__class__(
            path=self.path,  # string, immutable
            method=self.method,  # string, immutable
            definition=fast_deepcopy(self.definition),
            schema=self.schema.clone(),  # shallow copy
            verbose_name=self.verbose_name,  # string, immutable
            app=self.app,  # not deepcopyable
            base_url=self.base_url,  # string, immutable
            path_parameters=fast_deepcopy(self.path_parameters),
            headers=fast_deepcopy(self.headers),
            cookies=fast_deepcopy(self.cookies),
            query=fast_deepcopy(self.query),
            body=fast_deepcopy(self.body),
        )

    def clone(self, **components: Any) -> APIOperation:
        """Create a new instance of this API operation with updated components."""
        return self.__class__(
            path=self.path,
            method=self.method,
            verbose_name=self.verbose_name,
            definition=self.definition,
            schema=self.schema,
            app=self.app,
            base_url=self.base_url,
            path_parameters=components["path_parameters"],
            query=components["query"],
            headers=components["headers"],
            cookies=components["cookies"],
            body=components["body"],
        )

    def make_case(
        self,
        *,
        path_parameters: PathParameters | None = None,
        headers: Headers | None = None,
        cookies: Cookies | None = None,
        query: Query | None = None,
        body: Body | NotSet = NOT_SET,
        media_type: str | None = None,
    ) -> C:
        """Create a new example for this API operation.

        The main use case is constructing Case instances completely manually, without data generation.
        """
        return self.schema.make_case(
            case_cls=self.case_cls,
            operation=self,
            path_parameters=path_parameters,
            headers=headers,
            cookies=cookies,
            query=query,
            body=body,
            media_type=media_type,
        )

    @property
    def operation_reference(self) -> str:
        path = self.path.replace("~", "~0").replace("/", "~1")
        return f"#/paths/{path}/{self.method}"

    def validate_response(self, response: Response) -> bool | None:
        """Validate API response for conformance.

        :raises CheckFailed: If the response does not conform to the API schema.
        """
        return self.schema.validate_response(self, response)

    def is_response_valid(self, response: Response) -> bool:
        """Validate API response for conformance."""
        try:
            self.validate_response(response)
            return True
        except CheckFailed:
            return False

    def get_raw_payload_schema(self, media_type: str) -> dict[str, Any] | None:
        return self.schema._get_payload_schema(self.definition.raw, media_type)

    def get_resolved_payload_schema(self, media_type: str) -> dict[str, Any] | None:
        return self.schema._get_payload_schema(self.definition.resolved, media_type)


# backward-compatibility
Endpoint = APIOperation


class Status(str, Enum):
    """Status of an action or multiple actions."""

    success = "success"
    failure = "failure"
    error = "error"
    skip = "skip"


@dataclass(repr=False)
class Check:
    """Single check run result."""

    name: str
    value: Status
    response: Response | None
    elapsed: float
    example: Case
    message: str | None = None
    # Failure-specific context
    context: FailureContext | None = None
    request: requests.PreparedRequest | None = None


@dataclass
class Interaction:
    """A single interaction with the target app."""

    request: Request
    response: Response
    checks: list[Check]
    status: Status
    data_generation_method: DataGenerationMethod
    recorded_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())

    @classmethod
    def from_requests(cls, case: Case, response: Response, status: Status, checks: list[Check]) -> Interaction:
        # Todo - rename
        return cls(
            request=response.request,
            response=response,
            status=status,
            checks=checks,
            data_generation_method=cast(DataGenerationMethod, case.data_generation_method),
        )

    @classmethod
    def from_wsgi(
        cls,
        case: Case,
        response: WSGIResponse,
        headers: dict[str, Any],
        elapsed: float,
        status: Status,
        checks: list[Check],
    ) -> Interaction:
        # todo: re-check
        import requests

        session = requests.Session()
        session.headers.update(headers)
        return cls(
            request=Request.from_case(case, session),
            response=Response.from_werkzeug(response, elapsed),
            status=status,
            checks=checks,
            data_generation_method=cast(DataGenerationMethod, case.data_generation_method),
        )


@dataclass(repr=False)
class TestResult:
    """Result of a single test."""

    __test__ = False

    method: str
    path: str
    verbose_name: str
    data_generation_method: list[DataGenerationMethod]
    checks: list[Check] = field(default_factory=list)
    errors: list[Exception] = field(default_factory=list)
    interactions: list[Interaction] = field(default_factory=list)
    logs: list[LogRecord] = field(default_factory=list)
    is_errored: bool = False
    is_flaky: bool = False
    is_skipped: bool = False
    skip_reason: str | None = None
    is_executed: bool = False
    # DEPRECATED: Seed is the same per test run
    seed: int | None = None

    def mark_errored(self) -> None:
        self.is_errored = True

    def mark_flaky(self) -> None:
        self.is_flaky = True

    def mark_skipped(self, exc: SkipTest | unittest.case.SkipTest | None) -> None:
        self.is_skipped = True
        if exc is not None:
            self.skip_reason = str(exc)

    def mark_executed(self) -> None:
        self.is_executed = True

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def has_failures(self) -> bool:
        return any(check.value == Status.failure for check in self.checks)

    @property
    def has_logs(self) -> bool:
        return bool(self.logs)

    def add_success(self, name: str, example: Case, response: Response, elapsed: float) -> Check:
        check = Check(
            name=name, value=Status.success, response=response, elapsed=elapsed, example=example, request=None
        )
        self.checks.append(check)
        return check

    def add_failure(
        self,
        name: str,
        example: Case,
        response: Response | None,
        elapsed: float,
        message: str,
        context: FailureContext | None,
        # todo: replace with serialized
        request: requests.PreparedRequest | None = None,
    ) -> Check:
        check = Check(
            name=name,
            value=Status.failure,
            response=response,
            elapsed=elapsed,
            example=example,
            message=message,
            context=context,
            request=request,
        )
        self.checks.append(check)
        return check

    def add_error(self, exception: Exception) -> None:
        self.errors.append(exception)

    def store_response(self, case: Case, response: Response, status: Status, checks: list[Check]) -> None:
        self.interactions.append(Interaction.from_requests(case, response, status, checks))

    def store_wsgi_response(
        self,
        case: Case,
        response: WSGIResponse,
        headers: dict[str, Any],
        elapsed: float,
        status: Status,
        checks: list[Check],
    ) -> None:
        # todo: drop
        self.interactions.append(Interaction.from_wsgi(case, response, headers, elapsed, status, checks))


@dataclass(repr=False)
class TestResultSet:
    """Set of multiple test results."""

    __test__ = False

    seed: int | None
    results: list[TestResult] = field(default_factory=list)
    generic_errors: list[OperationSchemaError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __iter__(self) -> Iterator[TestResult]:
        return iter(self.results)

    @property
    def is_empty(self) -> bool:
        """If the result set contains no results."""
        return len(self.results) == 0 and len(self.generic_errors) == 0

    @property
    def has_failures(self) -> bool:
        """If any result has any failures."""
        return any(result.has_failures for result in self)

    @property
    def has_errors(self) -> bool:
        """If any result has any errors."""
        return self.errored_count > 0

    @property
    def has_logs(self) -> bool:
        """If any result has any captured logs."""
        return any(result.has_logs for result in self)

    def _count(self, predicate: Callable) -> int:
        return sum(1 for result in self if predicate(result))

    @property
    def passed_count(self) -> int:
        return self._count(lambda result: not result.has_errors and not result.is_skipped and not result.has_failures)

    @property
    def skipped_count(self) -> int:
        return self._count(lambda result: result.is_skipped)

    @property
    def failed_count(self) -> int:
        return self._count(lambda result: result.has_failures and not result.is_errored)

    @property
    def errored_count(self) -> int:
        return self._count(lambda result: result.has_errors or result.is_errored) + len(self.generic_errors)

    @property
    def total(self) -> dict[str, dict[str | Status, int]]:
        """An aggregated statistic about test results."""
        output: dict[str, dict[str | Status, int]] = {}
        for item in self.results:
            for check in item.checks:
                output.setdefault(check.name, Counter())
                output[check.name][check.value] += 1
                output[check.name]["total"] += 1
        # Avoid using Counter, since its behavior could harm in other places:
        # `if not total["unknown"]:` - this will lead to the branch execution
        # It is better to let it fail if there is a wrong key
        return {key: dict(value) for key, value in output.items()}

    def append(self, item: TestResult) -> None:
        """Add a new item to the results list."""
        self.results.append(item)

    def add_warning(self, warning: str) -> None:
        """Add a new warning to the warnings list."""
        self.warnings.append(warning)


CheckFunction = Callable[["Response", Case], Optional[bool]]
