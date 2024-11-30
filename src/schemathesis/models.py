from __future__ import annotations

import http.client
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from functools import partial
from itertools import chain
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Generic,
    Iterator,
    Literal,
    NoReturn,
    Type,
    TypeVar,
    cast,
)
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from schemathesis.checks import CHECKS
from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.errors import IncorrectUsage, InvalidSchema, SerializationNotPossible
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.core.transport import USER_AGENT

from . import code_samples, serializers
from ._override import CaseOverride
from .constants import (
    SCHEMATHESIS_TEST_CASE_HEADER,
    SERIALIZERS_SUGGESTION_MESSAGE,
)
from .generation import DataGenerationMethod, GenerationConfig, generate_random_case_id
from .hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher, dispatch
from .internal.checks import CheckContext
from .internal.diff import diff
from .internal.output import prepare_response_payload
from .parameters import Parameter, ParameterSet, PayloadAlternatives
from .sanitization import sanitize_url, sanitize_value
from .transports import PreparedRequestData, RequestsTransport, prepare_request_data

if TYPE_CHECKING:
    import requests.auth
    from hypothesis import strategies as st
    from requests.structures import CaseInsensitiveDict

    from .auths import AuthStorage
    from .internal.checks import CheckFunction
    from .schemas import BaseSchema
    from .serializers import Serializer
    from .transports.responses import GenericResponse


@dataclass
class TransitionId:
    name: str
    status_code: str

    __slots__ = ("name", "status_code")


@dataclass
class CaseSource:
    """Data sources, used to generate a test case."""

    case: Case
    response: GenericResponse
    elapsed: float
    overrides_all_parameters: bool
    transition_id: TransitionId


def cant_serialize(media_type: str) -> NoReturn:  # type: ignore
    """Reject the current example if we don't know how to send this data to the application."""
    from hypothesis import event, note, reject

    event_text = f"Can't serialize data to `{media_type}`."
    note(f"{event_text} {SERIALIZERS_SUGGESTION_MESSAGE}")
    event(event_text)
    reject()  # type: ignore


class TestPhase(str, Enum):
    __test__ = False

    EXPLICIT = "explicit"
    COVERAGE = "coverage"
    GENERATE = "generate"


@dataclass
class GenerationMetadata:
    """Stores various information about how data is generated."""

    query: DataGenerationMethod | None
    path_parameters: DataGenerationMethod | None
    headers: DataGenerationMethod | None
    cookies: DataGenerationMethod | None
    body: DataGenerationMethod | None
    phase: TestPhase
    # Temporary attributes to carry info specific to the coverage phase
    description: str | None
    location: str | None
    parameter: str | None
    parameter_location: str | None

    __slots__ = (
        "query",
        "path_parameters",
        "headers",
        "cookies",
        "body",
        "phase",
        "description",
        "location",
        "parameter",
        "parameter_location",
    )


@dataclass(repr=False)
class Case:
    """A single test case parameters."""

    operation: APIOperation
    # Time spent on generation of this test case
    generation_time: float
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

    meta: GenerationMetadata | None = None

    # The way the case was generated (None for manually crafted ones)
    data_generation_method: DataGenerationMethod | None = None
    _auth: requests.auth.AuthBase | None = None
    _has_explicit_auth: bool = False
    _explicit_method: str | None = None

    def __post_init__(self) -> None:
        self._original_path_parameters = self.path_parameters.copy() if self.path_parameters else None
        self._original_headers = self.headers.copy() if self.headers else None
        self._original_cookies = self.cookies.copy() if self.cookies else None
        self._original_query = self.query.copy() if self.query else None

    def asdict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "generation_time": self.generation_time,
            "verbose_name": self.operation.verbose_name,
            "path_template": self.path,
            "path_parameters": self.path_parameters,
            "query": self.query,
            "cookies": self.cookies,
            "media_type": self.media_type,
        }

    def _has_generated_component(self, name: str) -> bool:
        assert name in ["path_parameters", "headers", "cookies", "query"]
        if self.meta is None:
            return False
        return getattr(self.meta, name) is not None

    def _get_diff(self, component: Literal["path_parameters", "headers", "query", "cookies"]) -> dict[str, Any]:
        original = getattr(self, f"_original_{component}")
        current = getattr(self, component)
        if not (current and original):
            return {}
        original_value = original if self._has_generated_component(component) else {}
        return diff(original_value, current)

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

    @property
    def _override(self) -> CaseOverride:
        return CaseOverride(
            path_parameters=self._get_diff("path_parameters"),
            headers=self._get_diff("headers"),
            query=self._get_diff("query"),
            cookies=self._get_diff("cookies"),
        )

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    @property
    def path(self) -> str:
        return self.operation.path

    @property
    def full_path(self) -> str:
        return self.operation.full_path

    @property
    def method(self) -> str:
        return self._explicit_method.upper() if self._explicit_method else self.operation.method.upper()

    @property
    def base_url(self) -> str | None:
        return self.operation.base_url

    @property
    def app(self) -> Any:
        return self.operation.app

    def set_source(
        self,
        response: GenericResponse,
        case: Case,
        elapsed: float,
        overrides_all_parameters: bool,
        transition_id: TransitionId,
    ) -> None:
        self.source = CaseSource(
            case=case,
            response=response,
            elapsed=elapsed,
            overrides_all_parameters=overrides_all_parameters,
            transition_id=transition_id,
        )

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
        if self.operation.schema.output_config.sanitize:
            kwargs["url"] = sanitize_url(kwargs["url"])
            sanitize_value(kwargs["headers"])
            if kwargs["cookies"]:
                sanitize_value(kwargs["cookies"])
            if kwargs["params"]:
                sanitize_value(kwargs["params"])
        return prepare_request_data(kwargs)

    def as_curl_command(self, headers: dict[str, Any] | None = None, verify: bool = True) -> str:
        """Construct a curl command for a given case."""
        request_data = self.prepare_code_sample_data(headers)
        case_headers = None
        if self.headers is not None:
            case_headers = dict(self.headers)
        return code_samples.generate(
            method=request_data.method,
            url=request_data.url,
            body=request_data.body,
            headers=case_headers,
            verify=verify,
            extra_headers=request_data.headers,
        )

    def _get_base_url(self, base_url: str | None = None) -> str:
        if base_url is None:
            if self.base_url is not None:
                base_url = self.base_url
            else:
                raise ValueError(
                    "Base URL is required as `base_url` argument in `call` or should be specified "
                    "in the schema constructor as a part of Schema URL."
                )
        return base_url

    def _get_headers(self, headers: dict[str, str] | None = None) -> CaseInsensitiveDict:
        from requests.structures import CaseInsensitiveDict

        final_headers = self.headers.copy() if self.headers is not None else CaseInsensitiveDict()
        if headers:
            final_headers.update(headers)
        final_headers.setdefault("User-Agent", USER_AGENT)
        final_headers.setdefault(SCHEMATHESIS_TEST_CASE_HEADER, self.id)
        return final_headers

    def _get_serializer(self, media_type: str | None = None) -> Serializer | None:
        """Get a serializer for the payload, if there is any."""
        input_media_type = media_type or self.media_type
        if input_media_type is not None:
            media_type = serializers.get_first_matching_media_type(input_media_type)
            if media_type is None:
                # This media type is set manually. Otherwise, it should have been rejected during the data generation
                raise SerializationNotPossible.for_media_type(input_media_type)
            # SAFETY: It is safe to assume that serializer will be found, because `media_type` returned above
            # is registered. This intentionally ignores cases with concurrent serializers registry modification.
            cls = cast(Type[serializers.Serializer], serializers.get(media_type))
            return cls()
        return None

    def _get_body(self) -> list | dict[str, Any] | str | int | float | bool | bytes | NotSet:
        return self.body

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
    ) -> GenericResponse:
        hook_context = HookContext(operation=self.operation)
        dispatch("before_call", hook_context, self, **kwargs)
        response = self.operation.schema.transport.send(
            self, session=session, base_url=base_url, headers=headers, params=params, cookies=cookies, **kwargs
        )
        dispatch("after_call", hook_context, self, response)
        return response

    def validate_response(
        self,
        response: GenericResponse,
        checks: list[CheckFunction] | None = None,
        additional_checks: list[CheckFunction] | None = None,
        excluded_checks: list[CheckFunction] | None = None,
        headers: dict[str, Any] | None = None,
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

        from .transports.responses import get_payload

        checks = checks or CHECKS.get_all()
        checks = [check for check in checks if check not in (excluded_checks or [])]
        for check in additional_checks or []:
            if check not in checks and check not in (excluded_checks or []):
                checks.append(check)
        failures: set[Failure] = set()
        ctx = CheckContext(
            override=self._override, auth=None, headers=CaseInsensitiveDict(headers) if headers else None
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
            payload = get_payload(response)
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
    ) -> GenericResponse:
        __tracebackhide__ = True
        response = self.call(base_url, session, headers, **kwargs)
        self.validate_response(
            response,
            checks,
            headers=headers,
            additional_checks=additional_checks,
            excluded_checks=excluded_checks,
        )
        return response

    def _get_url(self, base_url: str | None) -> str:
        base_url = self._get_base_url(base_url)
        formatted_path = self.formatted_path.lstrip("/")
        if not base_url.endswith("/"):
            base_url += "/"
        return unquote(urljoin(base_url, quote(formatted_path)))

    def get_full_url(self) -> str:
        """Make a full URL to the current API operation, including query parameters."""
        import requests

        base_url = self.base_url or "http://127.0.0.1"
        kwargs = RequestsTransport().serialize_case(self, base_url=base_url)
        request = requests.Request(**kwargs)
        prepared = requests.Session().prepare_request(request)  # type: ignore
        return cast(str, prepared.url)


P = TypeVar("P", bound=Parameter)
D = TypeVar("D", bound=dict)


@dataclass(repr=False)
class OperationDefinition(Generic[D]):
    """A wrapper to store not resolved API operation definitions.

    To prevent recursion errors we need to store definitions without resolving references. But operation definitions
    itself can be behind a reference (when there is a ``$ref`` in ``paths`` values), therefore we need to store this
    scope change to have a proper reference resolving later.
    """

    raw: D
    resolved: D
    scope: str

    __slots__ = ("raw", "resolved", "scope")

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...


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

    def get_strategies_from_examples(
        self, as_strategy_kwargs: dict[str, Any] | None = None
    ) -> list[st.SearchStrategy[Case]]:
        """Get examples from the API operation."""
        return self.schema.get_strategies_from_examples(self, as_strategy_kwargs=as_strategy_kwargs)

    def get_parameter_serializer(self, location: str) -> Callable | None:
        """Get a function that serializes parameters for the given location.

        It handles serializing data into various `collectionFormat` options and similar.
        Note that payload is handled by this function - it is handled by serializers.
        """
        return self.schema.get_parameter_serializer(self, location)

    def prepare_multipart(self, form_data: dict[str, Any]) -> tuple[list | None, dict[str, Any] | None]:
        return self.schema.prepare_multipart(form_data, self)

    def get_request_payload_content_types(self) -> list[str]:
        return self.schema.get_request_payload_content_types(self)

    def _get_default_media_type(self) -> str:
        # If the user wants to send payload, then there should be a media type, otherwise the payload is ignored
        media_types = self.get_request_payload_content_types()
        if len(media_types) == 1:
            # The only available option
            return media_types[0]
        media_types_repr = ", ".join(media_types)
        raise IncorrectUsage(
            "Can not detect appropriate media type. "
            "You can either specify one of the defined media types "
            f"or pass any other media type available for serialization. Defined media types: {media_types_repr}"
        )

    def make_case(
        self,
        *,
        path_parameters: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: list | dict[str, Any] | str | int | float | bool | bytes | NotSet = NOT_SET,
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

    def validate_response(self, response: GenericResponse) -> bool | None:
        """Validate API response for conformance.

        :raises FailureGroup: If the response does not conform to the API schema.
        """
        return self.schema.validate_response(self, response)

    def is_response_valid(self, response: GenericResponse) -> bool:
        """Validate API response for conformance."""
        try:
            self.validate_response(response)
            return True
        except AssertionError:
            return False

    def get_raw_payload_schema(self, media_type: str) -> dict[str, Any] | None:
        return self.schema._get_payload_schema(self.definition.raw, media_type)

    def get_resolved_payload_schema(self, media_type: str) -> dict[str, Any] | None:
        return self.schema._get_payload_schema(self.definition.resolved, media_type)
