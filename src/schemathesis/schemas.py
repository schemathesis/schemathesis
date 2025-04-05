from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property, lru_cache, partial
from itertools import chain
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Generator,
    Generic,
    Iterator,
    NoReturn,
    TypeVar,
)
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit, urlunsplit

from schemathesis import transport
from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.errors import IncorrectUsage, InvalidSchema
from schemathesis.core.output import OutputConfig
from schemathesis.core.rate_limit import build_limiter
from schemathesis.core.result import Ok, Result
from schemathesis.core.transport import Response
from schemathesis.generation import GenerationConfig, GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis import strategies
from schemathesis.generation.hypothesis.given import GivenInput, given_proxy
from schemathesis.generation.meta import CaseMetadata
from schemathesis.hooks import HookDispatcherMark

from .auths import AuthStorage
from .filters import (
    FilterSet,
    FilterValue,
    MatcherFunc,
    RegexValue,
    is_deprecated,
)
from .hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher, HookScope, dispatch, to_filterable_hook

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy
    from pyrate_limiter import Limiter
    from typing_extensions import Self

    from schemathesis.core import Specification
    from schemathesis.generation.stateful.state_machine import APIStateMachine


C = TypeVar("C", bound=Case)


@lru_cache
def get_full_path(base_path: str, path: str) -> str:
    return unquote(urljoin(base_path, quote(path.lstrip("/"))))


@dataclass
class FilteredCount:
    """Count of total items and those passing filters."""

    total: int
    selected: int

    __slots__ = ("total", "selected")

    def __init__(self) -> None:
        self.total = 0
        self.selected = 0


@dataclass
class ApiStatistic:
    """Statistics about API operations and links."""

    operations: FilteredCount
    links: FilteredCount

    __slots__ = ("operations", "links")

    def __init__(self) -> None:
        self.operations = FilteredCount()
        self.links = FilteredCount()


@dataclass
class ApiOperationsCount:
    """Statistics about API operations."""

    total: int
    selected: int

    __slots__ = ("total", "selected")

    def __init__(self) -> None:
        self.total = 0
        self.selected = 0


@dataclass(eq=False)
class BaseSchema(Mapping):
    raw_schema: dict[str, Any]
    location: str | None = None
    base_url: str | None = None
    filter_set: FilterSet = field(default_factory=FilterSet)
    app: Any = None
    hooks: HookDispatcher = field(default_factory=lambda: HookDispatcher(scope=HookScope.SCHEMA))
    auth: AuthStorage = field(default_factory=AuthStorage)
    test_function: Callable | None = None
    generation_config: GenerationConfig = field(default_factory=GenerationConfig)
    output_config: OutputConfig = field(default_factory=OutputConfig)
    rate_limiter: Limiter | None = None

    def __post_init__(self) -> None:
        self.hook = to_filterable_hook(self.hooks)  # type: ignore[method-assign]

    @property
    def specification(self) -> Specification:
        raise NotImplementedError

    @property
    def transport(self) -> transport.BaseTransport:
        return transport.get(self.app)

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    def include(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
        tag: FilterValue | None = None,
        tag_regex: RegexValue | None = None,
        operation_id: FilterValue | None = None,
        operation_id_regex: RegexValue | None = None,
    ) -> BaseSchema:
        """Include only operations that match the given filters."""
        filter_set = self.filter_set.clone()
        filter_set.include(
            func,
            name=name,
            name_regex=name_regex,
            method=method,
            method_regex=method_regex,
            path=path,
            path_regex=path_regex,
            tag=tag,
            tag_regex=tag_regex,
            operation_id=operation_id,
            operation_id_regex=operation_id_regex,
        )
        return self.clone(filter_set=filter_set)

    def exclude(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
        tag: FilterValue | None = None,
        tag_regex: RegexValue | None = None,
        operation_id: FilterValue | None = None,
        operation_id_regex: RegexValue | None = None,
        deprecated: bool = False,
    ) -> BaseSchema:
        """Include only operations that match the given filters."""
        filter_set = self.filter_set.clone()
        if deprecated:
            if func is None:
                func = is_deprecated
            else:
                filter_set.exclude(is_deprecated)
        filter_set.exclude(
            func,
            name=name,
            name_regex=name_regex,
            method=method,
            method_regex=method_regex,
            path=path,
            path_regex=path_regex,
            tag=tag,
            tag_regex=tag_regex,
            operation_id=operation_id,
            operation_id_regex=operation_id_regex,
        )
        return self.clone(filter_set=filter_set)

    def __iter__(self) -> Iterator[str]:
        raise NotImplementedError

    def __getitem__(self, item: str) -> APIOperationMap:
        __tracebackhide__ = True
        try:
            return self._get_operation_map(item)
        except KeyError as exc:
            self.on_missing_operation(item, exc)

    def _get_operation_map(self, key: str) -> APIOperationMap:
        raise NotImplementedError

    def on_missing_operation(self, item: str, exc: KeyError) -> NoReturn:
        raise NotImplementedError

    def __len__(self) -> int:
        return self.statistic.operations.total

    def hook(self, hook: str | Callable) -> Callable:
        return self.hooks.register(hook)

    def get_full_path(self, path: str) -> str:
        """Compute full path for the given path."""
        return get_full_path(self.base_path, path)

    @property
    def base_path(self) -> str:
        """Base path for the schema."""
        # if `base_url` is specified, then it should include base path
        # Example: http://127.0.0.1:8080/api
        if self.base_url:
            path = urlsplit(self.base_url).path
        else:
            path = self._get_base_path()
        if not path.endswith("/"):
            path += "/"
        return path

    def _get_base_path(self) -> str:
        raise NotImplementedError

    def _build_base_url(self) -> str:
        path = self._get_base_path()
        parts = urlsplit(self.location or "")[:2] + (path, "", "")
        return urlunsplit(parts)

    def get_base_url(self) -> str:
        base_url = self.base_url
        if base_url is not None:
            return base_url.rstrip("/")
        return self._build_base_url()

    def validate(self) -> None:
        raise NotImplementedError

    @cached_property
    def statistic(self) -> ApiStatistic:
        return self._measure_statistic()

    def _measure_statistic(self) -> ApiStatistic:
        raise NotImplementedError

    def get_all_operations(
        self, generation_config: GenerationConfig | None = None
    ) -> Generator[Result[APIOperation, InvalidSchema], None, None]:
        raise NotImplementedError

    def get_strategies_from_examples(self, operation: APIOperation, **kwargs: Any) -> list[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        raise NotImplementedError

    def get_security_requirements(self, operation: APIOperation) -> list[str]:
        """Get applied security requirements for the given API operation."""
        raise NotImplementedError

    def get_parameter_serializer(self, operation: APIOperation, location: str) -> Callable | None:
        """Get a function that serializes parameters for the given location."""
        raise NotImplementedError

    def parametrize(self) -> Callable:
        """Mark a test function as a parametrized one."""

        def wrapper(func: Callable) -> Callable:
            from schemathesis.pytest.plugin import SchemaHandleMark

            if SchemaHandleMark.is_set(func):

                def wrapped_test(*_: Any, **__: Any) -> NoReturn:
                    raise IncorrectUsage(
                        f"You have applied `parametrize` to the `{func.__name__}` test more than once, which "
                        "overrides the previous decorator. "
                        "The `parametrize` decorator could be applied to the same function at most once."
                    )

                return wrapped_test
            HookDispatcher.add_dispatcher(func)
            cloned = self.clone(test_function=func)
            SchemaHandleMark.set(func, cloned)
            return func

        return wrapper

    def given(self, *args: GivenInput, **kwargs: GivenInput) -> Callable:
        """Proxy Hypothesis strategies to ``hypothesis.given``."""
        return given_proxy(*args, **kwargs)

    def clone(
        self, *, test_function: Callable | NotSet = NOT_SET, filter_set: FilterSet | NotSet = NOT_SET
    ) -> BaseSchema:
        if isinstance(test_function, NotSet):
            _test_function = self.test_function
        else:
            _test_function = test_function
        if isinstance(filter_set, NotSet):
            _filter_set = self.filter_set
        else:
            _filter_set = filter_set

        return self.__class__(
            self.raw_schema,
            location=self.location,
            base_url=self.base_url,
            app=self.app,
            hooks=self.hooks,
            auth=self.auth,
            test_function=_test_function,
            generation_config=self.generation_config,
            output_config=self.output_config,
            rate_limiter=self.rate_limiter,
            filter_set=_filter_set,
        )

    def get_local_hook_dispatcher(self) -> HookDispatcher | None:
        """Get a HookDispatcher instance bound to the test if present."""
        # It might be not present when it is used without pytest via `APIOperation.as_strategy()`
        if self.test_function is not None:
            # Might be missing it in case of `LazySchema` usage
            return HookDispatcherMark.get(self.test_function)
        return None

    def dispatch_hook(self, name: str, context: HookContext, *args: Any, **kwargs: Any) -> None:
        """Dispatch a hook via all available dispatchers."""
        dispatch(name, context, *args, **kwargs)
        self.hooks.dispatch(name, context, *args, **kwargs)
        local_dispatcher = self.get_local_hook_dispatcher()
        if local_dispatcher is not None:
            local_dispatcher.dispatch(name, context, *args, **kwargs)

    def prepare_multipart(
        self, form_data: dict[str, Any], operation: APIOperation
    ) -> tuple[list | None, dict[str, Any] | None]:
        """Split content of `form_data` into files & data.

        Forms may contain file fields, that we should send via `files` argument in `requests`.
        """
        raise NotImplementedError

    def get_request_payload_content_types(self, operation: APIOperation) -> list[str]:
        raise NotImplementedError

    def make_case(
        self,
        *,
        operation: APIOperation,
        method: str | None = None,
        path: str | None = None,
        path_parameters: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: list | dict[str, Any] | str | int | float | bool | bytes | NotSet = NOT_SET,
        media_type: str | None = None,
        meta: CaseMetadata | None = None,
    ) -> Case:
        raise NotImplementedError

    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: HookDispatcher | None = None,
        auth_storage: AuthStorage | None = None,
        generation_mode: GenerationMode = GenerationMode.default(),
        generation_config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> SearchStrategy:
        raise NotImplementedError

    def as_state_machine(self) -> type[APIStateMachine]:
        """Create a state machine class."""
        raise NotImplementedError

    def get_links(self, operation: APIOperation) -> dict[str, dict[str, Any]]:
        raise NotImplementedError

    def get_tags(self, operation: APIOperation) -> list[str] | None:
        raise NotImplementedError

    def validate_response(self, operation: APIOperation, response: Response) -> bool | None:
        raise NotImplementedError

    def prepare_schema(self, schema: Any) -> Any:
        raise NotImplementedError

    def _get_payload_schema(self, definition: dict[str, Any], media_type: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def as_strategy(
        self,
        hooks: HookDispatcher | None = None,
        auth_storage: AuthStorage | None = None,
        generation_mode: GenerationMode = GenerationMode.default(),
        generation_config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> SearchStrategy:
        """Build a strategy for generating test cases for all defined API operations."""
        _strategies = [
            operation.ok().as_strategy(
                hooks=hooks,
                auth_storage=auth_storage,
                generation_mode=generation_mode,
                generation_config=generation_config,
                **kwargs,
            )
            for operation in self.get_all_operations()
            if isinstance(operation, Ok)
        ]
        return strategies.combine(_strategies)

    def configure(
        self,
        *,
        base_url: str | None | NotSet = NOT_SET,
        location: str | None | NotSet = NOT_SET,
        rate_limit: str | None | NotSet = NOT_SET,
        generation: GenerationConfig | NotSet = NOT_SET,
        output: OutputConfig | NotSet = NOT_SET,
        app: Any | NotSet = NOT_SET,
    ) -> Self:
        if not isinstance(base_url, NotSet):
            if base_url is not None:
                validate_base_url(base_url)
            self.base_url = base_url
        if not isinstance(location, NotSet):
            self.location = location
        if not isinstance(rate_limit, NotSet):
            if isinstance(rate_limit, str):
                self.rate_limiter = build_limiter(rate_limit)
            else:
                self.rate_limiter = None
        if not isinstance(generation, NotSet):
            self.generation_config = generation
        if not isinstance(output, NotSet):
            self.output_config = output
        if not isinstance(app, NotSet):
            self.app = app
        return self


INVALID_BASE_URL_MESSAGE = (
    "The provided base URL is invalid. This URL serves as a prefix for all API endpoints you want to test. "
    "Make sure it is a properly formatted URL."
)


def validate_base_url(value: str) -> None:
    try:
        netloc = urlparse(value).netloc
    except ValueError as exc:
        raise ValueError(INVALID_BASE_URL_MESSAGE) from exc
    if value and not netloc:
        raise ValueError(INVALID_BASE_URL_MESSAGE)


@dataclass
class APIOperationMap(Mapping):
    _schema: BaseSchema
    _data: Mapping

    def __getitem__(self, item: str) -> APIOperation:
        return self._data[item]

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def as_strategy(
        self,
        hooks: HookDispatcher | None = None,
        auth_storage: AuthStorage | None = None,
        generation_mode: GenerationMode = GenerationMode.default(),
        generation_config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> SearchStrategy:
        """Build a strategy for generating test cases for all API operations defined in this subset."""
        _strategies = [
            operation.as_strategy(
                hooks=hooks,
                auth_storage=auth_storage,
                generation_mode=generation_mode,
                generation_config=generation_config,
                **kwargs,
            )
            for operation in self._data.values()
        ]
        return strategies.combine(_strategies)


@dataclass(eq=False)
class Parameter:
    """A logically separate parameter bound to a location (e.g., to "query string").

    For example, if the API requires multiple headers to be present, each header is presented as a separate
    `Parameter` instance.
    """

    # The parameter definition in the language acceptable by the API
    definition: Any

    @property
    def location(self) -> str:
        """Where this parameter is located.

        E.g. "query" or "body"
        """
        raise NotImplementedError

    @property
    def name(self) -> str:
        """Parameter name."""
        raise NotImplementedError

    @property
    def is_required(self) -> bool:
        """Whether the parameter is required for a successful API call."""
        raise NotImplementedError

    def serialize(self, operation: APIOperation) -> str:
        """Get parameter's string representation."""
        raise NotImplementedError


P = TypeVar("P", bound=Parameter)


@dataclass
class ParameterSet(Generic[P]):
    """A set of parameters for the same location."""

    items: list[P] = field(default_factory=list)

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    def add(self, parameter: P) -> None:
        """Add a new parameter."""
        self.items.append(parameter)

    def get(self, name: str) -> P | None:
        for parameter in self:
            if parameter.name == name:
                return parameter
        return None

    def contains(self, name: str) -> bool:
        return self.get(name) is not None

    def __contains__(self, item: str) -> bool:
        return self.contains(item)

    def __bool__(self) -> bool:
        return bool(self.items)

    def __iter__(self) -> Generator[P, None, None]:
        yield from iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, item: int) -> P:
        return self.items[item]


class PayloadAlternatives(ParameterSet[P]):
    """A set of alternative payloads."""


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


@dataclass(eq=False)
class APIOperation(Generic[P]):
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
    label: str = None  # type: ignore
    app: Any = None
    base_url: str | None = None
    path_parameters: ParameterSet[P] = field(default_factory=ParameterSet)
    headers: ParameterSet[P] = field(default_factory=ParameterSet)
    cookies: ParameterSet[P] = field(default_factory=ParameterSet)
    query: ParameterSet[P] = field(default_factory=ParameterSet)
    body: PayloadAlternatives[P] = field(default_factory=PayloadAlternatives)

    def __post_init__(self) -> None:
        if self.label is None:
            self.label = f"{self.method.upper()} {self.path}"  # type: ignore

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
        generation_mode: GenerationMode = GenerationMode.default(),
        generation_config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> SearchStrategy[Case]:
        """Turn this API operation into a Hypothesis strategy."""
        strategy = self.schema.get_case_strategy(
            self, hooks, auth_storage, generation_mode, generation_config=generation_config, **kwargs
        )

        def _apply_hooks(dispatcher: HookDispatcher, _strategy: SearchStrategy[Case]) -> SearchStrategy[Case]:
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

    def get_strategies_from_examples(self, **kwargs: Any) -> list[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        kwargs.setdefault("generation_config", self.schema.generation_config)
        return self.schema.get_strategies_from_examples(self, **kwargs)

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

    def Case(
        self,
        *,
        method: str | None = None,
        path_parameters: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: list | dict[str, Any] | str | int | float | bool | bytes | NotSet = NOT_SET,
        media_type: str | None = None,
        meta: CaseMetadata | None = None,
    ) -> Case:
        """Create a new example for this API operation.

        The main use case is constructing Case instances completely manually, without data generation.
        """
        return self.schema.make_case(
            operation=self,
            method=method,
            path_parameters=path_parameters,
            headers=headers,
            cookies=cookies,
            query=query,
            body=body,
            media_type=media_type,
            meta=meta,
        )

    @property
    def operation_reference(self) -> str:
        path = self.path.replace("~", "~0").replace("/", "~1")
        return f"#/paths/{path}/{self.method}"

    def validate_response(self, response: Response) -> bool | None:
        """Validate API response for conformance.

        :raises FailureGroup: If the response does not conform to the API schema.
        """
        return self.schema.validate_response(self, response)

    def is_response_valid(self, response: Response) -> bool:
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
