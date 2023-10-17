import base64
import datetime
import http
import inspect
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from functools import partial
from itertools import chain
from logging import LogRecord
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Generator,
    Generic,
    Iterator,
    List,
    NoReturn,
    Optional,
    Sequence,
    Tuple,
    Type,
    TypeVar,
    Union,
    cast,
)
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit, urlunsplit

import requests.auth
import werkzeug
from hypothesis import event, note, reject
from hypothesis import strategies as st
from requests.structures import CaseInsensitiveDict
from starlette_testclient import TestClient as ASGIClient

from . import failures, serializers
from .auths import AuthStorage
from .code_samples import CodeSampleStyle
from .constants import (
    DEFAULT_RESPONSE_TIMEOUT,
    SCHEMATHESIS_TEST_CASE_HEADER,
    SERIALIZERS_SUGGESTION_MESSAGE,
    USER_AGENT,
    DataGenerationMethod,
)
from .exceptions import (
    CheckFailed,
    FailureContext,
    OperationSchemaError,
    SerializationNotPossible,
    deduplicate_failed_checks,
    get_grouped_exception,
    get_timeout_error,
)
from .hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher, dispatch
from .masking import mask_request, mask_response
from .parameters import Parameter, ParameterSet, PayloadAlternatives
from .serializers import Serializer, SerializerContext
from .types import Body, Cookies, FormData, Headers, NotSet, PathParameters, Query
from .utils import (
    NOT_SET,
    GenericResponse,
    WSGIResponse,
    copy_response,
    deprecated_property,
    fast_deepcopy,
    generate_random_case_id,
    get_response_payload,
    maybe_set_assertion_message,
)

if TYPE_CHECKING:
    from .schemas import BaseSchema
    from .stateful import Stateful, StatefulTest


@dataclass
class CaseSource:
    """Data sources, used to generate a test case."""

    case: "Case"
    response: GenericResponse
    elapsed: float

    def partial_deepcopy(self) -> "CaseSource":
        return self.__class__(
            case=self.case.partial_deepcopy(), response=copy_response(self.response), elapsed=self.elapsed
        )


def cant_serialize(media_type: str) -> NoReturn:  # type: ignore
    """Reject the current example if we don't know how to send this data to the application."""
    event_text = f"Can't serialize data to `{media_type}`."
    note(f"{event_text} {SERIALIZERS_SUGGESTION_MESSAGE}")
    event(event_text)
    reject()  # type: ignore


REQUEST_SIGNATURE = inspect.signature(requests.Request)


@dataclass()
class PreparedRequestData:
    method: str
    url: str
    body: Optional[Union[str, bytes]]
    headers: Headers


def prepare_request_data(kwargs: Dict[str, Any]) -> PreparedRequestData:
    """Prepare request data for generating code samples."""
    kwargs = {key: value for key, value in kwargs.items() if key in REQUEST_SIGNATURE.parameters}
    request = requests.Request(**kwargs).prepare()
    return PreparedRequestData(
        method=str(request.method), url=str(request.url), body=request.body, headers=dict(request.headers)
    )


@dataclass(repr=False)
class Case:
    """A single test case parameters."""

    operation: "APIOperation"
    # Unique test case identifier
    id: str = field(default_factory=generate_random_case_id, compare=False)
    path_parameters: Optional[PathParameters] = None
    headers: Optional[CaseInsensitiveDict] = None
    cookies: Optional[Cookies] = None
    query: Optional[Query] = None
    # By default, there is no body, but we can't use `None` as the default value because it clashes with `null`
    # which is a valid payload.
    body: Union[Body, NotSet] = NOT_SET
    # The media type for cases with a payload. For example, "application/json"
    media_type: Optional[str] = None
    source: Optional[CaseSource] = None

    # The way the case was generated (None for manually crafted ones)
    data_generation_method: Optional[DataGenerationMethod] = None
    _auth: Optional[requests.auth.AuthBase] = None

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
    def endpoint(self) -> "APIOperation":
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
    def base_url(self) -> Optional[str]:
        return self.operation.base_url

    @property
    def app(self) -> Any:
        return self.operation.app

    def set_source(self, response: GenericResponse, case: "Case", elapsed: float) -> None:
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
        except ValueError as exc:
            # A single unmatched `}` inside the path template may cause this
            raise OperationSchemaError(f"Malformed path template: `{self.path}`\n\n  {exc}") from exc

    def get_full_base_url(self) -> Optional[str]:
        """Create a full base url, adding "localhost" for WSGI apps."""
        parts = urlsplit(self.base_url)
        if not parts.hostname:
            path = cast(str, parts.path or "")
            return urlunsplit(("http", "localhost", path or "", "", ""))
        return self.base_url

    def prepare_code_sample_data(self, headers: Optional[Dict[str, Any]]) -> PreparedRequestData:
        base_url = self.get_full_base_url()
        kwargs = self.as_requests_kwargs(base_url, headers=headers)
        return prepare_request_data(kwargs)

    def get_code_to_reproduce(
        self,
        headers: Optional[Dict[str, Any]] = None,
        request: Optional[requests.PreparedRequest] = None,
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

    def as_curl_command(self, headers: Optional[Dict[str, Any]] = None, verify: bool = True) -> str:
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

    def _get_base_url(self, base_url: Optional[str] = None) -> str:
        if base_url is None:
            if self.base_url is not None:
                base_url = self.base_url
            else:
                raise ValueError(
                    "Base URL is required as `base_url` argument in `call` or should be specified "
                    "in the schema constructor as a part of Schema URL."
                )
        return base_url

    def _get_headers(self, headers: Optional[Dict[str, str]] = None) -> CaseInsensitiveDict:
        final_headers = self.headers.copy() if self.headers is not None else CaseInsensitiveDict()
        if headers:
            final_headers.update(headers)
        final_headers.setdefault("User-Agent", USER_AGENT)
        final_headers.setdefault(SCHEMATHESIS_TEST_CASE_HEADER, self.id)
        return final_headers

    def _get_serializer(self) -> Optional[Serializer]:
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

    def as_requests_kwargs(
        self, base_url: Optional[str] = None, headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Convert the case into a dictionary acceptable by requests."""
        final_headers = self._get_headers(headers)
        if self.media_type and self.media_type != "multipart/form-data" and not isinstance(self.body, NotSet):
            # `requests` will handle multipart form headers with the proper `boundary` value.
            if "content-type" not in {header.lower() for header in final_headers}:
                final_headers["Content-Type"] = self.media_type
        base_url = self._get_base_url(base_url)
        formatted_path = self.formatted_path.lstrip("/")
        if not base_url.endswith("/"):
            base_url += "/"
        url = unquote(urljoin(base_url, quote(formatted_path)))
        extra: Dict[str, Any]
        serializer = self._get_serializer()
        if serializer is not None and not isinstance(self.body, NotSet):
            context = SerializerContext(case=self)
            extra = serializer.as_requests(context, self.body)
        else:
            extra = {}
        if self._auth is not None:
            extra["auth"] = self._auth
        additional_headers = extra.pop("headers", None)
        if additional_headers:
            # Additional headers, needed for the serializer
            for key, value in additional_headers.items():
                final_headers.setdefault(key, value)
        return {
            "method": self.method,
            "url": url,
            "cookies": self.cookies,
            "headers": final_headers,
            "params": self.query,
            **extra,
        }

    def call(
        self,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
        headers: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        cookies: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Make a network call with `requests`."""
        hook_context = HookContext(operation=self.operation)
        dispatch("before_call", hook_context, self)
        data = self.as_requests_kwargs(base_url, headers)
        data.update(kwargs)
        if params is not None:
            _merge_dict_to(data, "params", params)
        if cookies is not None:
            _merge_dict_to(data, "cookies", cookies)
        data.setdefault("timeout", DEFAULT_RESPONSE_TIMEOUT / 1000)
        if session is None:
            validate_vanilla_requests_kwargs(data)
            session = requests.Session()
            close_session = True
        else:
            close_session = False
        verify = data.get("verify", True)
        try:
            with self.operation.schema.ratelimit():
                response = session.request(**data)  # type: ignore
        except requests.Timeout as exc:
            timeout = 1000 * data["timeout"]  # It is defined and not empty, since the exception happened
            request = cast(requests.PreparedRequest, exc.request)
            code_message = self._get_code_message(self.operation.schema.code_sample_style, request, verify=verify)
            raise get_timeout_error(timeout)(
                f"\n\n1. Request timed out after {timeout:.2f}ms\n\n----------\n\n{code_message}",
                context=failures.RequestTimeout(timeout=timeout),
            ) from None
        response.verify = verify  # type: ignore[attr-defined]
        dispatch("after_call", hook_context, self, response)
        if close_session:
            session.close()
        return response

    def as_werkzeug_kwargs(self, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Convert the case into a dictionary acceptable by werkzeug.Client."""
        final_headers = self._get_headers(headers)
        if self.media_type and not isinstance(self.body, NotSet):
            # If we need to send a payload, then the Content-Type header should be set
            final_headers["Content-Type"] = self.media_type
        extra: Dict[str, Any]
        serializer = self._get_serializer()
        if serializer is not None and not isinstance(self.body, NotSet):
            context = SerializerContext(case=self)
            extra = serializer.as_werkzeug(context, self.body)
        else:
            extra = {}
        return {
            "method": self.method,
            "path": self.operation.schema.get_full_path(self.formatted_path),
            # Convert to a regular dictionary, as we use `CaseInsensitiveDict` which is not supported by Werkzeug
            "headers": dict(final_headers),
            "query_string": self.query,
            **extra,
        }

    def call_wsgi(
        self,
        app: Any = None,
        headers: Optional[Dict[str, str]] = None,
        query_string: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> WSGIResponse:
        application = app or self.app
        if application is None:
            raise RuntimeError(
                "WSGI application instance is required. "
                "Please, set `app` argument in the schema constructor or pass it to `call_wsgi`"
            )
        hook_context = HookContext(operation=self.operation)
        dispatch("before_call", hook_context, self)
        data = self.as_werkzeug_kwargs(headers)
        if query_string is not None:
            _merge_dict_to(data, "query_string", query_string)
        client = werkzeug.Client(application, WSGIResponse)
        with cookie_handler(client, self.cookies), self.operation.schema.ratelimit():
            response = client.open(**data, **kwargs)
        requests_kwargs = self.as_requests_kwargs(base_url=self.get_full_base_url(), headers=headers)
        response.request = requests.Request(**requests_kwargs).prepare()
        dispatch("after_call", hook_context, self, response)
        return response

    def call_asgi(
        self,
        app: Any = None,
        base_url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> requests.Response:
        application = app or self.app
        if application is None:
            raise RuntimeError(
                "ASGI application instance is required. "
                "Please, set `app` argument in the schema constructor or pass it to `call_asgi`"
            )
        if base_url is None:
            base_url = self.get_full_base_url()

        with ASGIClient(application) as client:
            return self.call(base_url=base_url, session=client, headers=headers, **kwargs)

    def validate_response(
        self,
        response: GenericResponse,
        checks: Tuple["CheckFunction", ...] = (),
        additional_checks: Tuple["CheckFunction", ...] = (),
        excluded_checks: Tuple["CheckFunction", ...] = (),
        code_sample_style: Optional[str] = None,
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

        checks = checks or ALL_CHECKS
        checks = tuple(check for check in checks if check not in excluded_checks)
        additional_checks = tuple(check for check in additional_checks if check not in excluded_checks)
        failed_checks = []
        for check in chain(checks, additional_checks):
            copied_case = self.partial_deepcopy()
            copied_response = copy_response(response)
            try:
                check(copied_response, copied_case)
            except AssertionError as exc:
                maybe_set_assertion_message(exc, check.__name__)
                failed_checks.append(exc)
        failed_checks = list(deduplicate_failed_checks(failed_checks))
        if failed_checks:
            exception_cls = get_grouped_exception(self.operation.verbose_name, *failed_checks)
            formatted_failures = "\n\n".join(f"{idx}. {error.args[0]}" for idx, error in enumerate(failed_checks, 1))
            code_sample_style = (
                CodeSampleStyle.from_str(code_sample_style)
                if code_sample_style is not None
                else self.operation.schema.code_sample_style
            )
            verify = getattr(response, "verify", True)
            if self.operation.schema.mask_sensitive_output:
                mask_request(response.request)
                mask_response(response)
            code_message = self._get_code_message(code_sample_style, response.request, verify=verify)
            payload = get_response_payload(response)
            raise exception_cls(
                f"\n\n{formatted_failures}\n\n"
                f"----------\n\n"
                f"Response status: {response.status_code}\n"
                f"Response payload: `{payload}`\n\n"
                f"{code_message}",
                causes=tuple(failed_checks),
            )

    def _get_code_message(
        self, code_sample_style: CodeSampleStyle, request: requests.PreparedRequest, verify: bool
    ) -> str:
        if code_sample_style == CodeSampleStyle.python:
            code = self.get_code_to_reproduce(request=request, verify=verify)
            return f"Run this Python code to reproduce this response: \n\n    {code}\n"
        if code_sample_style == CodeSampleStyle.curl:
            code = self.as_curl_command(headers=dict(request.headers), verify=verify)
            return f"Run this cURL command to reproduce this response: \n\n    {code}\n"
        raise ValueError(f"Unknown code sample style: {code_sample_style.name}")

    def call_and_validate(
        self,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
        headers: Optional[Dict[str, Any]] = None,
        checks: Tuple["CheckFunction", ...] = (),
        code_sample_style: Optional[str] = None,
        **kwargs: Any,
    ) -> requests.Response:
        __tracebackhide__ = True
        response = self.call(base_url, session, headers, **kwargs)
        self.validate_response(response, checks, code_sample_style=code_sample_style)
        return response

    def get_full_url(self) -> str:
        """Make a full URL to the current API operation, including query parameters."""
        base_url = self.base_url or "http://127.0.0.1"
        kwargs = self.as_requests_kwargs(base_url)
        request = requests.Request(**kwargs)
        prepared = requests.Session().prepare_request(request)  # type: ignore
        return cast(str, prepared.url)

    def partial_deepcopy(self) -> "Case":
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
        )


def _merge_dict_to(data: Dict[str, Any], data_key: str, new: Dict[str, Any]) -> None:
    original = data[data_key] or {}
    for key, value in new.items():
        original[key] = value
    data[data_key] = original


def validate_vanilla_requests_kwargs(data: Dict[str, Any]) -> None:
    """Check arguments for `requests.Session.request`.

    Some arguments can be valid for cases like ASGI integration, but at the same time they won't work for the regular
    `requests` calls. In such cases we need to avoid an obscure error message, that comes from `requests`.
    """
    url = data["url"]
    if not urlparse(url).netloc:
        raise RuntimeError(
            "The URL should be absolute, so Schemathesis knows where to send the data. \n"
            f"If you use the ASGI integration, please supply your test client "
            f"as the `session` argument to `call`.\nURL: {url}"
        )


@contextmanager
def cookie_handler(client: werkzeug.Client, cookies: Optional[Cookies]) -> Generator[None, None, None]:
    """Set cookies required for a call."""
    if not cookies:
        yield
    else:
        for key, value in cookies.items():
            client.set_cookie("localhost", key, value)
        yield
        for key in cookies:
            client.delete_cookie("localhost", key)


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

    def __contains__(self, item: Union[str, int]) -> bool:
        return item in self.resolved

    def __getitem__(self, item: Union[str, int]) -> Union[None, bool, float, str, list, Dict[str, Any]]:
        return self.resolved[item]

    def get(self, item: Union[str, int], default: Any = None) -> Union[None, bool, float, str, list, Dict[str, Any]]:
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
    schema: "BaseSchema"
    verbose_name: str = None  # type: ignore
    app: Any = None
    base_url: Optional[str] = None
    path_parameters: ParameterSet[P] = field(default_factory=ParameterSet)
    headers: ParameterSet[P] = field(default_factory=ParameterSet)
    cookies: ParameterSet[P] = field(default_factory=ParameterSet)
    query: ParameterSet[P] = field(default_factory=ParameterSet)
    body: PayloadAlternatives[P] = field(default_factory=PayloadAlternatives)
    case_cls: Type[C] = Case  # type: ignore

    def __post_init__(self) -> None:
        if self.verbose_name is None:
            self.verbose_name = f"{self.method.upper()} {self.full_path}"  # type: ignore

    @property
    def full_path(self) -> str:
        return self.schema.get_full_path(self.path)

    @property
    def links(self) -> Dict[str, Dict[str, Any]]:
        return self.schema.get_links(self)

    def iter_parameters(self) -> Iterator[P]:
        """Iterate over all operation's parameters."""
        return chain(self.path_parameters, self.headers, self.cookies, self.query)

    def _lookup_container(self, location: str) -> Union[ParameterSet[P], PayloadAlternatives[P], None]:
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

    def get_parameter(self, name: str, location: str) -> Optional[P]:
        container = self._lookup_container(location)
        if container is not None:
            return container.get(name)
        return None

    def as_strategy(
        self,
        hooks: Optional["HookDispatcher"] = None,
        auth_storage: Optional[AuthStorage] = None,
        data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
        **kwargs: Any,
    ) -> st.SearchStrategy:
        """Turn this API operation into a Hypothesis strategy."""
        strategy = self.schema.get_case_strategy(self, hooks, auth_storage, data_generation_method, **kwargs)

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

    def get_security_requirements(self) -> List[str]:
        return self.schema.get_security_requirements(self)

    def get_strategies_from_examples(self) -> List[st.SearchStrategy[Case]]:
        """Get examples from the API operation."""
        return self.schema.get_strategies_from_examples(self)

    def get_stateful_tests(self, response: GenericResponse, stateful: Optional["Stateful"]) -> Sequence["StatefulTest"]:
        return self.schema.get_stateful_tests(response, self, stateful)

    def get_parameter_serializer(self, location: str) -> Optional[Callable]:
        """Get a function that serializes parameters for the given location.

        It handles serializing data into various `collectionFormat` options and similar.
        Note that payload is handled by this function - it is handled by serializers.
        """
        return self.schema.get_parameter_serializer(self, location)

    def prepare_multipart(self, form_data: FormData) -> Tuple[Optional[List], Optional[Dict[str, Any]]]:
        return self.schema.prepare_multipart(form_data, self)

    def get_request_payload_content_types(self) -> List[str]:
        return self.schema.get_request_payload_content_types(self)

    def partial_deepcopy(self) -> "APIOperation":
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

    def clone(self, **components: Any) -> "APIOperation":
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
        path_parameters: Optional[PathParameters] = None,
        headers: Optional[Headers] = None,
        cookies: Optional[Cookies] = None,
        query: Optional[Query] = None,
        body: Union[Body, NotSet] = NOT_SET,
        media_type: Optional[str] = None,
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

    def validate_response(self, response: GenericResponse) -> None:
        """Validate API response for conformance.

        :raises CheckFailed: If the response does not conform to the API schema.
        """
        return self.schema.validate_response(self, response)

    def is_response_valid(self, response: GenericResponse) -> bool:
        """Validate API response for conformance."""
        try:
            self.validate_response(response)
            return True
        except CheckFailed:
            return False

    def get_raw_payload_schema(self, media_type: str) -> Optional[Dict[str, Any]]:
        return self.schema._get_payload_schema(self.definition.raw, media_type)

    def get_resolved_payload_schema(self, media_type: str) -> Optional[Dict[str, Any]]:
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
    response: Optional[GenericResponse]
    elapsed: float
    example: Case
    message: Optional[str] = None
    # Failure-specific context
    context: Optional[FailureContext] = None
    request: Optional[requests.PreparedRequest] = None


@dataclass(repr=False)
class Request:
    """Request data extracted from `Case`."""

    method: str
    uri: str
    body: Optional[str]
    headers: Headers

    @classmethod
    def from_case(cls, case: Case, session: requests.Session) -> "Request":
        """Create a new `Request` instance from `Case`."""
        base_url = case.get_full_base_url()
        kwargs = case.as_requests_kwargs(base_url)
        request = requests.Request(**kwargs)
        prepared = session.prepare_request(request)  # type: ignore
        return cls.from_prepared_request(prepared)

    @classmethod
    def from_prepared_request(cls, prepared: requests.PreparedRequest) -> "Request":
        """A prepared request version is already stored in `requests.Response`."""
        body = prepared.body

        if isinstance(body, str):
            # can be a string for `application/x-www-form-urlencoded`
            body = body.encode("utf-8")

        # these values have `str` type at this point
        uri = cast(str, prepared.url)
        method = cast(str, prepared.method)
        return cls(
            uri=uri,
            method=method,
            headers={key: [value] for (key, value) in prepared.headers.items()},
            body=serialize_payload(body) if body is not None else body,
        )


def serialize_payload(payload: bytes) -> str:
    return base64.b64encode(payload).decode()


@dataclass(repr=False)
class Response:
    """Unified response data."""

    status_code: int
    message: str
    headers: Dict[str, List[str]]
    body: Optional[str]
    encoding: Optional[str]
    http_version: str
    elapsed: float
    verify: bool

    @classmethod
    def from_requests(cls, response: requests.Response) -> "Response":
        """Create a response from requests.Response."""
        headers = {name: response.raw.headers.getlist(name) for name in response.raw.headers.keys()}
        # Similar to http.client:319 (HTTP version detection in stdlib's `http` package)
        http_version = "1.0" if response.raw.version == 10 else "1.1"

        def is_empty(_response: requests.Response) -> bool:
            # Assume the response is empty if:
            #   - no `Content-Length` header
            #   - no chunks when iterating over its content
            return "Content-Length" not in headers and list(_response.iter_content()) == []

        body = None if is_empty(response) else serialize_payload(response.content)
        return cls(
            status_code=response.status_code,
            message=response.reason,
            body=body,
            encoding=response.encoding,
            headers=headers,
            http_version=http_version,
            elapsed=response.elapsed.total_seconds(),
            verify=getattr(response, "verify", True),
        )

    @classmethod
    def from_wsgi(cls, response: WSGIResponse, elapsed: float) -> "Response":
        """Create a response from WSGI response."""
        message = http.client.responses.get(response.status_code, "UNKNOWN")
        headers = {name: response.headers.getlist(name) for name in response.headers.keys()}
        # Note, this call ensures that `response.response` is a sequence, which is needed for comparison
        data = response.get_data()
        body = None if response.response == [] else serialize_payload(data)
        encoding: Optional[str]
        if body is not None:
            encoding = response.mimetype_params.get("charset", response.charset)
        else:
            encoding = None
        return cls(
            status_code=response.status_code,
            message=message,
            body=body,
            encoding=encoding,
            headers=headers,
            http_version="1.1",
            elapsed=elapsed,
            verify=True,
        )


@dataclass
class Interaction:
    """A single interaction with the target app."""

    request: Request
    response: Response
    checks: List[Check]
    status: Status
    data_generation_method: DataGenerationMethod
    recorded_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())

    @classmethod
    def from_requests(
        cls, case: Case, response: requests.Response, status: Status, checks: List[Check]
    ) -> "Interaction":
        return cls(
            request=Request.from_prepared_request(response.request),
            response=Response.from_requests(response),
            status=status,
            checks=checks,
            data_generation_method=cast(DataGenerationMethod, case.data_generation_method),
        )

    @classmethod
    def from_wsgi(
        cls,
        case: Case,
        response: WSGIResponse,
        headers: Dict[str, Any],
        elapsed: float,
        status: Status,
        checks: List[Check],
    ) -> "Interaction":
        session = requests.Session()
        session.headers.update(headers)
        return cls(
            request=Request.from_case(case, session),
            response=Response.from_wsgi(response, elapsed),
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
    data_generation_method: List[DataGenerationMethod]
    checks: List[Check] = field(default_factory=list)
    errors: List[Exception] = field(default_factory=list)
    interactions: List[Interaction] = field(default_factory=list)
    logs: List[LogRecord] = field(default_factory=list)
    is_errored: bool = False
    is_flaky: bool = False
    is_skipped: bool = False
    is_executed: bool = False
    seed: Optional[int] = None

    def mark_errored(self) -> None:
        self.is_errored = True

    def mark_flaky(self) -> None:
        self.is_flaky = True

    def mark_skipped(self) -> None:
        self.is_skipped = True

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

    def add_success(self, name: str, example: Case, response: GenericResponse, elapsed: float) -> Check:
        check = Check(
            name=name, value=Status.success, response=response, elapsed=elapsed, example=example, request=None
        )
        self.checks.append(check)
        return check

    def add_failure(
        self,
        name: str,
        example: Case,
        response: Optional[GenericResponse],
        elapsed: float,
        message: str,
        context: Optional[FailureContext],
        request: Optional[requests.PreparedRequest] = None,
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

    def store_requests_response(
        self, case: Case, response: requests.Response, status: Status, checks: List[Check]
    ) -> None:
        self.interactions.append(Interaction.from_requests(case, response, status, checks))

    def store_wsgi_response(
        self,
        case: Case,
        response: WSGIResponse,
        headers: Dict[str, Any],
        elapsed: float,
        status: Status,
        checks: List[Check],
    ) -> None:
        self.interactions.append(Interaction.from_wsgi(case, response, headers, elapsed, status, checks))


@dataclass(repr=False)
class TestResultSet:
    """Set of multiple test results."""

    __test__ = False

    results: List[TestResult] = field(default_factory=list)
    generic_errors: List[OperationSchemaError] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

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
    def total(self) -> Dict[str, Dict[Union[str, Status], int]]:
        """An aggregated statistic about test results."""
        output: Dict[str, Dict[Union[str, Status], int]] = {}
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


CheckFunction = Callable[[GenericResponse, Case], Optional[bool]]
