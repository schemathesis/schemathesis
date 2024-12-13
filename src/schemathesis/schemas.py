from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from functools import lru_cache
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ContextManager,
    Generator,
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
from schemathesis.generation.hypothesis import strategies
from schemathesis.generation.hypothesis.given import GivenInput, given_proxy
from schemathesis.hooks import HookDispatcherMark

from .auths import AuthStorage
from .filters import (
    FilterSet,
    FilterValue,
    MatcherFunc,
    RegexValue,
    is_deprecated,
)
from .generation import DataGenerationMethod, GenerationConfig
from .hooks import HookContext, HookDispatcher, HookScope, dispatch, to_filterable_hook
from .models import APIOperation, Case

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy
    from pyrate_limiter import Limiter
    from typing_extensions import Self

    from schemathesis.core import Specification
    from schemathesis.transport import Transport

    from .stateful.state_machine import APIStateMachine


C = TypeVar("C", bound=Case)


@lru_cache
def get_full_path(base_path: str, path: str) -> str:
    return unquote(urljoin(base_path, quote(path.lstrip("/"))))


@dataclass(eq=False)
class BaseSchema(Mapping):
    raw_schema: dict[str, Any]
    specification: Specification
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
    def transport(self) -> Transport:
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
        return self.operations_count

    def hook(self, hook: str | Callable) -> Callable:
        return self.hooks.register(hook)

    @property
    def verbose_name(self) -> str:
        raise NotImplementedError

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

    @property
    def operations_count(self) -> int:
        raise NotImplementedError

    @property
    def links_count(self) -> int:
        raise NotImplementedError

    def get_all_operations(
        self, generation_config: GenerationConfig | None = None
    ) -> Generator[Result[APIOperation, InvalidSchema], None, None]:
        raise NotImplementedError

    def get_strategies_from_examples(
        self, operation: APIOperation, as_strategy_kwargs: dict[str, Any] | None = None
    ) -> list[SearchStrategy[Case]]:
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
            specification=self.specification,
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
        case_cls: type[C],
        operation: APIOperation,
        path_parameters: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: list | dict[str, Any] | str | int | float | bool | bytes | NotSet = NOT_SET,
        media_type: str | None = None,
    ) -> C:
        raise NotImplementedError

    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: HookDispatcher | None = None,
        auth_storage: AuthStorage | None = None,
        data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
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

    def ratelimit(self) -> ContextManager:
        """Limit the rate of sending generated requests."""
        label = urlparse(self.base_url).netloc
        if self.rate_limiter is not None:
            self.rate_limiter.try_acquire(label)
        return nullcontext()

    def _get_payload_schema(self, definition: dict[str, Any], media_type: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def as_strategy(
        self,
        hooks: HookDispatcher | None = None,
        auth_storage: AuthStorage | None = None,
        data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
        generation_config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> SearchStrategy:
        """Build a strategy for generating test cases for all defined API operations."""
        _strategies = [
            operation.ok().as_strategy(
                hooks=hooks,
                auth_storage=auth_storage,
                data_generation_method=data_generation_method,
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
        data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
        generation_config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> SearchStrategy:
        """Build a strategy for generating test cases for all API operations defined in this subset."""
        _strategies = [
            operation.as_strategy(
                hooks=hooks,
                auth_storage=auth_storage,
                data_generation_method=data_generation_method,
                generation_config=generation_config,
                **kwargs,
            )
            for operation in self._data.values()
        ]
        return strategies.combine(_strategies)
