from __future__ import annotations

from collections.abc import Callable, Generator, Iterator, Mapping
from dataclasses import dataclass, field
from functools import cached_property, lru_cache, partial
from itertools import chain
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    NoReturn,
    TypeVar,
)
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from schemathesis import transport
from schemathesis.config import ProjectConfig
from schemathesis.core import NOT_SET, NotSet, media_types
from schemathesis.core.adapter import OperationParameter, ResponsesContainer
from schemathesis.core.errors import IncorrectUsage, InvalidSchema
from schemathesis.core.result import Ok, Result
from schemathesis.core.transport import Response
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis.given import GivenInput, given_proxy
from schemathesis.generation.meta import CaseMetadata
from schemathesis.hooks import HookDispatcherMark, _should_skip_hook

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
    import httpx
    import requests
    from hypothesis.strategies import SearchStrategy
    from requests.structures import CaseInsensitiveDict
    from werkzeug.test import TestResponse

    from schemathesis.auths import AuthContext
    from schemathesis.core import Specification
    from schemathesis.generation.stateful.state_machine import APIStateMachine
    from schemathesis.resources import ExtraDataSource


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
    config: ProjectConfig
    location: str | None = None
    filter_set: FilterSet = field(default_factory=FilterSet)
    app: Any = None
    hooks: HookDispatcher = field(default_factory=lambda: HookDispatcher(scope=HookScope.SCHEMA))
    auth: AuthStorage = field(default_factory=AuthStorage)
    test_function: Callable | None = None

    def __post_init__(self) -> None:
        self.hook = to_filterable_hook(self.hooks)  # type: ignore[method-assign]

    @property
    def specification(self) -> Specification:
        raise NotImplementedError

    @property
    def transport(self) -> transport.BaseTransport:
        return transport.get(self.app)

    def apply_auth(self, case: Case, context: AuthContext) -> bool:
        """Apply spec-specific authentication to a test case.

        Returns True if authentication was applied, False otherwise.
        Subclasses should implement this to provide spec-specific auth mechanisms.
        """
        raise NotImplementedError

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
        """Return a new schema containing only operations matching the specified criteria.

        Args:
            func: Custom filter function that accepts operation context.
            name: Operation name(s) to include.
            name_regex: Regex pattern for operation names.
            method: HTTP method(s) to include.
            method_regex: Regex pattern for HTTP methods.
            path: API path(s) to include.
            path_regex: Regex pattern for API paths.
            tag: OpenAPI tag(s) to include.
            tag_regex: Regex pattern for OpenAPI tags.
            operation_id: Operation ID(s) to include.
            operation_id_regex: Regex pattern for operation IDs.

        Returns:
            New schema instance with applied include filters.

        """
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
        """Return a new schema excluding operations matching the specified criteria.

        Args:
            func: Custom filter function that accepts operation context.
            name: Operation name(s) to exclude.
            name_regex: Regex pattern for operation names.
            method: HTTP method(s) to exclude.
            method_regex: Regex pattern for HTTP methods.
            path: API path(s) to exclude.
            path_regex: Regex pattern for API paths.
            tag: OpenAPI tag(s) to exclude.
            tag_regex: Regex pattern for OpenAPI tags.
            operation_id: Operation ID(s) to exclude.
            operation_id_regex: Regex pattern for operation IDs.
            deprecated: Whether to exclude deprecated operations.

        Returns:
            New schema instance with applied exclude filters.

        """
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
        """Register a hook function for this schema only.

        Args:
            hook: Hook name string or hook function to register.

        """
        return self.hooks.hook(hook)

    def get_full_path(self, path: str) -> str:
        return get_full_path(self.base_path, path)

    @property
    def base_path(self) -> str:
        """Base path for the schema."""
        # if `base_url` is specified, then it should include base path
        # Example: http://127.0.0.1:8080/api
        if self.config.base_url:
            path = urlsplit(self.config.base_url).path
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

    @cached_property
    def _cached_base_url(self) -> str:
        """Cached base URL computation since schema doesn't change."""
        return self._build_base_url()

    def get_base_url(self) -> str:
        base_url = self.config.base_url
        if base_url is not None:
            return base_url.rstrip("/")
        return self._cached_base_url

    def validate(self) -> None:
        raise NotImplementedError

    @cached_property
    def statistic(self) -> ApiStatistic:
        return self._measure_statistic()

    def _measure_statistic(self) -> ApiStatistic:
        raise NotImplementedError

    def get_all_operations(self) -> Generator[Result[APIOperation, InvalidSchema], None, None]:
        raise NotImplementedError

    def get_strategies_from_examples(self, operation: APIOperation, **kwargs: Any) -> list[SearchStrategy[Case]]:
        raise NotImplementedError

    def get_parameter_serializer(self, operation: APIOperation, location: str) -> Callable | None:
        raise NotImplementedError

    def parametrize(self) -> Callable:
        """Return a decorator that marks a test function for `pytest` parametrization.

        The decorated test function will be parametrized with test cases generated
        from the schema's API operations.

        Returns:
            Decorator function for test parametrization.

        Raises:
            IncorrectUsage: If applied to the same function multiple times.

        """

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
        """Proxy to Hypothesis's `given` decorator for adding custom strategies.

        Args:
            *args: Positional arguments passed to `hypothesis.given`.
            **kwargs: Keyword arguments passed to `hypothesis.given`.

        """
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
            config=self.config,
            location=self.location,
            app=self.app,
            hooks=self.hooks,
            auth=self.auth,
            test_function=_test_function,
            filter_set=_filter_set,
        )

    def get_local_hook_dispatcher(self) -> HookDispatcher | None:
        # It might be not present when it is used without pytest via `APIOperation.as_strategy()`
        if self.test_function is not None:
            # Might be missing it in case of `LazySchema` usage
            return HookDispatcherMark.get(self.test_function)
        return None

    def dispatch_hook(self, name: str, context: HookContext, *args: Any, **kwargs: Any) -> None:
        dispatch(name, context, *args, **kwargs)
        self.hooks.dispatch(name, context, *args, **kwargs)
        local_dispatcher = self.get_local_hook_dispatcher()
        if local_dispatcher is not None:
            local_dispatcher.dispatch(name, context, *args, **kwargs)

    def prepare_multipart(
        self, form_data: dict[str, Any], operation: APIOperation, selected_content_types: dict[str, str] | None = None
    ) -> tuple[list | None, dict[str, Any] | None]:
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
        headers: dict[str, Any] | CaseInsensitiveDict | None = None,
        cookies: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: list | dict[str, Any] | str | int | float | bool | bytes | NotSet = NOT_SET,
        media_type: str | None = None,
        multipart_content_types: dict[str, str] | None = None,
        meta: CaseMetadata | None = None,
    ) -> Case:
        raise NotImplementedError

    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: HookDispatcher | None = None,
        auth_storage: AuthStorage | None = None,
        generation_mode: GenerationMode = GenerationMode.POSITIVE,
        **kwargs: Any,
    ) -> SearchStrategy:
        raise NotImplementedError

    def as_state_machine(self) -> type[APIStateMachine]:
        """Create a state machine class for stateful testing of linked API operations.

        Returns:
            APIStateMachine subclass configured for this schema.

        """
        raise NotImplementedError

    def get_tags(self, operation: APIOperation) -> list[str] | None:
        raise NotImplementedError

    def create_extra_data_source(self) -> ExtraDataSource | None:
        """Create an extra data source for augmenting test generation with real data.

        Returns:
            ExtraDataSource instance or None if not supported by this schema type.

        """
        raise NotImplementedError

    def validate_response(
        self,
        operation: APIOperation,
        response: Response,
        *,
        case: Case | None = None,
    ) -> bool | None:
        raise NotImplementedError

    def as_strategy(
        self,
        generation_mode: GenerationMode = GenerationMode.POSITIVE,
        **kwargs: Any,
    ) -> SearchStrategy:
        """Create a Hypothesis strategy that generates test cases for all schema operations.

        Use with `@given` in non-Schemathesis tests.

        Args:
            generation_mode: Whether to generate positive or negative test data.
            **kwargs: Additional keywords for each strategy.

        Returns:
            Combined Hypothesis strategy for all valid operations in the schema.

        """
        from hypothesis import strategies as st

        _strategies = [
            operation.ok().as_strategy(generation_mode=generation_mode, **kwargs)
            for operation in self.get_all_operations()
            if isinstance(operation, Ok)
        ]
        return st.one_of(_strategies)

    def find_operation_by_label(self, label: str) -> APIOperation | None:
        raise NotImplementedError


@dataclass
class APIOperationMap(Mapping):
    _schema: BaseSchema
    _data: Mapping

    __slots__ = ("_schema", "_data")

    def __getitem__(self, item: str) -> APIOperation:
        return self._data[item]

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def as_strategy(
        self,
        generation_mode: GenerationMode = GenerationMode.POSITIVE,
        **kwargs: Any,
    ) -> SearchStrategy:
        """Create a Hypothesis strategy that generates test cases for all schema operations in this subset.

        Use with `@given` in non-Schemathesis tests.

        Args:
            generation_mode: Whether to generate positive or negative test data.
            **kwargs: Additional keywords for each strategy.

        Returns:
            Combined Hypothesis strategy for all valid operations in the schema.

        """
        from hypothesis import strategies as st

        _strategies = [
            operation.as_strategy(generation_mode=generation_mode, **kwargs) for operation in self._data.values()
        ]
        return st.one_of(_strategies)


P = TypeVar("P", bound=OperationParameter)


@dataclass
class ParameterSet(Generic[P]):
    """A set of parameters for the same location."""

    items: list[P]

    __slots__ = ("items",)

    def __init__(self, items: list[P] | None = None) -> None:
        self.items = items or []

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    def add(self, parameter: P) -> None:
        """Add a new parameter."""
        self.items.append(parameter)

    def get(self, name: str) -> P | None:
        for parameter in self:
            if parameter.name == name:
                return parameter
        return None

    def __contains__(self, name: str) -> bool:
        for parameter in self.items:
            if parameter.name == name:
                return True
        return False

    def __iter__(self) -> Generator[P, None, None]:
        yield from iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, item: int) -> P:
        return self.items[item]


class PayloadAlternatives(ParameterSet[P]):
    """A set of alternative payloads."""


R = TypeVar("R", bound=ResponsesContainer)
S = TypeVar("S")
D = TypeVar("D", bound=dict)


@dataclass(repr=False)
class OperationDefinition(Generic[D]):
    """A wrapper to store not resolved API operation definitions.

    To prevent recursion errors we need to store definitions without resolving references. But operation definitions
    itself can be behind a reference (when there is a ``$ref`` in ``paths`` values), therefore we need to store this
    scope change to have a proper reference resolving later.
    """

    raw: D

    __slots__ = ("raw",)

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...


@dataclass()
class APIOperation(Generic[P, R, S]):
    """An API operation (e.g., `GET /users`)."""

    # `path` does not contain `basePath`
    # Example <scheme>://<host>/<basePath>/users - "/users" is path
    # https://swagger.io/docs/specification/2-0/api-host-and-base-path/
    path: str
    method: str
    definition: OperationDefinition = field(repr=False)
    schema: BaseSchema
    responses: R
    security: S
    label: str = None  # type: ignore[assignment]
    app: Any = None
    base_url: str | None = None
    path_parameters: ParameterSet[P] = field(default_factory=ParameterSet)
    headers: ParameterSet[P] = field(default_factory=ParameterSet)
    cookies: ParameterSet[P] = field(default_factory=ParameterSet)
    query: ParameterSet[P] = field(default_factory=ParameterSet)
    body: PayloadAlternatives[P] = field(default_factory=PayloadAlternatives)

    def __post_init__(self) -> None:
        if self.label is None:
            self.label = f"{self.method.upper()} {self.path}"  # type: ignore[unreachable]

    def __deepcopy__(self, memo: dict) -> APIOperation[P, R, S]:
        return self

    def __hash__(self) -> int:
        return hash(self.label)

    def __eq__(self, value: object, /) -> bool:
        if not isinstance(value, APIOperation):
            return NotImplemented
        return self.label == value.label

    @property
    def full_path(self) -> str:
        return self.schema.get_full_path(self.path)

    @property
    def tags(self) -> list[str] | None:
        return self.schema.get_tags(self)

    def iter_parameters(self) -> Iterator[P]:
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

    def get_bodies_for_media_type(self, media_type: str) -> Iterator[P]:
        main_target, sub_target = media_types.parse(media_type)
        for body in self.body:
            main, sub = media_types.parse(body.media_type)  # type:ignore[attr-defined]
            if main in ("*", main_target) and sub in ("*", sub_target):
                yield body

    def as_strategy(
        self,
        generation_mode: GenerationMode = GenerationMode.POSITIVE,
        **kwargs: Any,
    ) -> SearchStrategy[Case]:
        """Create a Hypothesis strategy that generates test cases for this API operation.

        Use with `@given` in non-Schemathesis tests.

        Args:
            generation_mode: Whether to generate positive or negative test data.
            **kwargs: Extra arguments to the underlying strategy function.

        """
        if self.schema.config.headers:
            headers = kwargs.setdefault("headers", {})
            headers.update(self.schema.config.headers)
        strategy = self.schema.get_case_strategy(self, generation_mode=generation_mode, **kwargs)

        def _apply_hooks(dispatcher: HookDispatcher, _strategy: SearchStrategy[Case]) -> SearchStrategy[Case]:
            context = HookContext(operation=self)
            for hook in dispatcher.get_all_by_name("before_generate_case"):
                if _should_skip_hook(hook, context):
                    continue
                _strategy = hook(context, _strategy)
            for hook in dispatcher.get_all_by_name("filter_case"):
                if _should_skip_hook(hook, context):
                    continue
                hook = partial(hook, context)
                _strategy = _strategy.filter(hook)
            for hook in dispatcher.get_all_by_name("map_case"):
                if _should_skip_hook(hook, context):
                    continue
                hook = partial(hook, context)
                _strategy = _strategy.map(hook)
            for hook in dispatcher.get_all_by_name("flatmap_case"):
                if _should_skip_hook(hook, context):
                    continue
                hook = partial(hook, context)
                _strategy = _strategy.flatmap(hook)
            return _strategy

        strategy = _apply_hooks(GLOBAL_HOOK_DISPATCHER, strategy)
        strategy = _apply_hooks(self.schema.hooks, strategy)
        hooks = kwargs.get("hooks")
        if hooks is not None:
            strategy = _apply_hooks(hooks, strategy)
        return strategy

    def get_strategies_from_examples(self, **kwargs: Any) -> list[SearchStrategy[Case]]:
        return self.schema.get_strategies_from_examples(self, **kwargs)

    def get_parameter_serializer(self, location: str) -> Callable | None:
        return self.schema.get_parameter_serializer(self, location)

    def prepare_multipart(
        self, form_data: dict[str, Any], selected_content_types: dict[str, str] | None = None
    ) -> tuple[list | None, dict[str, Any] | None]:
        return self.schema.prepare_multipart(form_data, self, selected_content_types=selected_content_types)

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

    def validate_response(
        self,
        response: Response | httpx.Response | requests.Response | TestResponse,
        *,
        case: Case | None = None,
    ) -> bool | None:
        """Validate a response against the API schema.

        Args:
            response: The HTTP response to validate. Can be a `requests.Response`,
                `httpx.Response`, `werkzeug.test.TestResponse`, or `schemathesis.Response`.
            case: The generated test case related to the provided response.

        Raises:
            FailureGroup: If the response does not conform to the schema.

        """
        return self.schema.validate_response(self, Response.from_any(response), case=case)

    def is_valid_response(self, response: Response | httpx.Response | requests.Response | TestResponse) -> bool:
        """Check if the provided response is valid against the API schema.

        Args:
            response: The HTTP response to validate. Can be a `requests.Response`,
                `httpx.Response`, `werkzeug.test.TestResponse`, or `schemathesis.Response`.

        Returns:
            `True` if response is valid, `False` otherwise.

        """
        try:
            self.validate_response(response)
            return True
        except AssertionError:
            return False

    def Case(
        self,
        *,
        method: str | None = None,
        path_parameters: dict[str, Any] | None = None,
        headers: dict[str, Any] | CaseInsensitiveDict | None = None,
        cookies: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: list | dict[str, Any] | str | int | float | bool | bytes | NotSet = NOT_SET,
        media_type: str | None = None,
        multipart_content_types: dict[str, str] | None = None,
        _meta: CaseMetadata | None = None,
    ) -> Case:
        """Create a test case with specific data instead of generated values.

        Args:
            method: Override HTTP method.
            path_parameters: Override path variables.
            headers: Override HTTP headers.
            cookies: Override cookies.
            query: Override query parameters.
            body: Override request body.
            media_type: Override media type.
            multipart_content_types: Selected content types for multipart form properties.

        """
        from requests.structures import CaseInsensitiveDict

        return self.schema.make_case(
            operation=self,
            method=method,
            path_parameters=path_parameters or {},
            headers=CaseInsensitiveDict() if headers is None else CaseInsensitiveDict(headers),
            cookies=cookies or {},
            query=query or {},
            body=body,
            media_type=media_type,
            multipart_content_types=multipart_content_types,
            meta=_meta,
        )

    @property
    def operation_reference(self) -> str:
        path = self.path.replace("~", "~0").replace("/", "~1")
        return f"#/paths/{path}/{self.method}"
