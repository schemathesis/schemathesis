"""Schema objects provide a convenient interface to raw schemas.

Their responsibilities:
  - Provide a unified way to work with different types of schemas
  - Give all paths / methods combinations that are available directly from the schema;

They give only static definitions of paths.
"""

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
    Iterable,
    Iterator,
    NoReturn,
    Sequence,
    TypeVar,
)
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit, urlunsplit

import hypothesis
from hypothesis.strategies import SearchStrategy
from pyrate_limiter import Limiter

from ._dependency_versions import IS_PYRATE_LIMITER_ABOVE_3
from ._hypothesis import create_test
from .auths import AuthStorage
from .code_samples import CodeSampleStyle
from .constants import NOT_SET
from .exceptions import OperationSchemaError, UsageError
from .generation import (
    DEFAULT_DATA_GENERATION_METHODS,
    DataGenerationMethod,
    DataGenerationMethodInput,
    GenerationConfig,
)
from .hooks import HookContext, HookDispatcher, HookScope, dispatch
from .internal.output import OutputConfig
from .internal.result import Ok, Result
from .models import APIOperation, Case
from .stateful import Stateful, StatefulTest
from .stateful.state_machine import APIStateMachine
from .types import (
    Body,
    Cookies,
    Filter,
    FormData,
    GenericTest,
    Headers,
    NotSet,
    PathParameters,
    Query,
)
from .utils import PARAMETRIZE_MARKER, GivenInput, combine_strategies, given_proxy

if TYPE_CHECKING:
    from .transports import Transport
    from .transports.responses import GenericResponse


C = TypeVar("C", bound=Case)


@lru_cache
def get_full_path(base_path: str, path: str) -> str:
    return unquote(urljoin(base_path, quote(path.lstrip("/"))))


@dataclass(eq=False)
class BaseSchema(Mapping):
    raw_schema: dict[str, Any]
    transport: Transport
    location: str | None = None
    base_url: str | None = None
    method: Filter | None = None
    endpoint: Filter | None = None
    tag: Filter | None = None
    operation_id: Filter | None = None
    app: Any = None
    hooks: HookDispatcher = field(default_factory=lambda: HookDispatcher(scope=HookScope.SCHEMA))
    auth: AuthStorage = field(default_factory=AuthStorage)
    test_function: GenericTest | None = None
    validate_schema: bool = True
    skip_deprecated_operations: bool = False
    data_generation_methods: list[DataGenerationMethod] = field(
        default_factory=lambda: list(DEFAULT_DATA_GENERATION_METHODS)
    )
    generation_config: GenerationConfig = field(default_factory=GenerationConfig)
    output_config: OutputConfig = field(default_factory=OutputConfig)
    code_sample_style: CodeSampleStyle = CodeSampleStyle.default()
    rate_limiter: Limiter | None = None
    sanitize_output: bool = True

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
        self, hooks: HookDispatcher | None = None
    ) -> Generator[Result[APIOperation, OperationSchemaError], None, None]:
        raise NotImplementedError

    def get_strategies_from_examples(
        self, operation: APIOperation, as_strategy_kwargs: dict[str, Any] | None = None
    ) -> list[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        raise NotImplementedError

    def get_security_requirements(self, operation: APIOperation) -> list[str]:
        """Get applied security requirements for the given API operation."""
        raise NotImplementedError

    def get_stateful_tests(
        self, response: GenericResponse, operation: APIOperation, stateful: Stateful | None
    ) -> Sequence[StatefulTest]:
        """Get a list of additional tests, that should be executed after this response from the API operation."""
        raise NotImplementedError

    def get_parameter_serializer(self, operation: APIOperation, location: str) -> Callable | None:
        """Get a function that serializes parameters for the given location."""
        raise NotImplementedError

    def get_all_tests(
        self,
        func: Callable,
        settings: hypothesis.settings | None = None,
        generation_config: GenerationConfig | None = None,
        seed: int | None = None,
        as_strategy_kwargs: dict[str, Any] | Callable[[APIOperation], dict[str, Any]] | None = None,
        hooks: HookDispatcher | None = None,
        _given_kwargs: dict[str, GivenInput] | None = None,
    ) -> Generator[Result[tuple[APIOperation, Callable], OperationSchemaError], None, None]:
        """Generate all operations and Hypothesis tests for them."""
        for result in self.get_all_operations(hooks=hooks):
            if isinstance(result, Ok):
                operation = result.ok()
                _as_strategy_kwargs: dict[str, Any] | None
                if callable(as_strategy_kwargs):
                    _as_strategy_kwargs = as_strategy_kwargs(operation)
                else:
                    _as_strategy_kwargs = as_strategy_kwargs
                test = create_test(
                    operation=operation,
                    test=func,
                    settings=settings,
                    seed=seed,
                    data_generation_methods=self.data_generation_methods,
                    generation_config=generation_config,
                    as_strategy_kwargs=_as_strategy_kwargs,
                    _given_kwargs=_given_kwargs,
                )
                yield Ok((operation, test))
            else:
                yield result

    def parametrize(
        self,
        method: Filter | None = NOT_SET,
        endpoint: Filter | None = NOT_SET,
        tag: Filter | None = NOT_SET,
        operation_id: Filter | None = NOT_SET,
        validate_schema: bool | NotSet = NOT_SET,
        skip_deprecated_operations: bool | NotSet = NOT_SET,
        data_generation_methods: Iterable[DataGenerationMethod] | NotSet = NOT_SET,
        code_sample_style: str | NotSet = NOT_SET,
    ) -> Callable:
        """Mark a test function as a parametrized one."""
        _code_sample_style = (
            CodeSampleStyle.from_str(code_sample_style) if isinstance(code_sample_style, str) else code_sample_style
        )

        def wrapper(func: GenericTest) -> GenericTest:
            if hasattr(func, PARAMETRIZE_MARKER):

                def wrapped_test(*_: Any, **__: Any) -> NoReturn:
                    raise UsageError(
                        f"You have applied `parametrize` to the `{func.__name__}` test more than once, which "
                        "overrides the previous decorator. "
                        "The `parametrize` decorator could be applied to the same function at most once."
                    )

                return wrapped_test
            HookDispatcher.add_dispatcher(func)
            cloned = self.clone(
                test_function=func,
                method=method,
                endpoint=endpoint,
                tag=tag,
                operation_id=operation_id,
                validate_schema=validate_schema,
                skip_deprecated_operations=skip_deprecated_operations,
                data_generation_methods=data_generation_methods,
                code_sample_style=_code_sample_style,  # type: ignore
            )
            setattr(func, PARAMETRIZE_MARKER, cloned)
            return func

        return wrapper

    def given(self, *args: GivenInput, **kwargs: GivenInput) -> Callable:
        """Proxy Hypothesis strategies to ``hypothesis.given``."""
        return given_proxy(*args, **kwargs)

    def clone(
        self,
        *,
        base_url: str | None | NotSet = NOT_SET,
        test_function: GenericTest | None = None,
        method: Filter | None = NOT_SET,
        endpoint: Filter | None = NOT_SET,
        tag: Filter | None = NOT_SET,
        operation_id: Filter | None = NOT_SET,
        app: Any = NOT_SET,
        hooks: HookDispatcher | NotSet = NOT_SET,
        auth: AuthStorage | NotSet = NOT_SET,
        validate_schema: bool | NotSet = NOT_SET,
        skip_deprecated_operations: bool | NotSet = NOT_SET,
        data_generation_methods: DataGenerationMethodInput | NotSet = NOT_SET,
        generation_config: GenerationConfig | NotSet = NOT_SET,
        output_config: OutputConfig | NotSet = NOT_SET,
        code_sample_style: CodeSampleStyle | NotSet = NOT_SET,
        rate_limiter: Limiter | None = NOT_SET,
        sanitize_output: bool | NotSet | None = NOT_SET,
    ) -> BaseSchema:
        if base_url is NOT_SET:
            base_url = self.base_url
        if method is NOT_SET:
            method = self.method
        if endpoint is NOT_SET:
            endpoint = self.endpoint
        if tag is NOT_SET:
            tag = self.tag
        if operation_id is NOT_SET:
            operation_id = self.operation_id
        if app is NOT_SET:
            app = self.app
        if validate_schema is NOT_SET:
            validate_schema = self.validate_schema
        if skip_deprecated_operations is NOT_SET:
            skip_deprecated_operations = self.skip_deprecated_operations
        if hooks is NOT_SET:
            hooks = self.hooks
        if auth is NOT_SET:
            auth = self.auth
        if data_generation_methods is NOT_SET:
            data_generation_methods = self.data_generation_methods
        if generation_config is NOT_SET:
            generation_config = self.generation_config
        if output_config is NOT_SET:
            output_config = self.output_config
        if code_sample_style is NOT_SET:
            code_sample_style = self.code_sample_style
        if rate_limiter is NOT_SET:
            rate_limiter = self.rate_limiter
        if sanitize_output is NOT_SET:
            sanitize_output = self.sanitize_output

        return self.__class__(
            self.raw_schema,
            location=self.location,
            base_url=base_url,  # type: ignore
            method=method,
            endpoint=endpoint,
            tag=tag,
            operation_id=operation_id,
            app=app,
            hooks=hooks,  # type: ignore
            auth=auth,  # type: ignore
            test_function=test_function,
            validate_schema=validate_schema,  # type: ignore
            skip_deprecated_operations=skip_deprecated_operations,  # type: ignore
            data_generation_methods=data_generation_methods,  # type: ignore
            generation_config=generation_config,  # type: ignore
            output_config=output_config,  # type: ignore
            code_sample_style=code_sample_style,  # type: ignore
            rate_limiter=rate_limiter,  # type: ignore
            sanitize_output=sanitize_output,  # type: ignore
            transport=self.transport,
        )

    def get_local_hook_dispatcher(self) -> HookDispatcher | None:
        """Get a HookDispatcher instance bound to the test if present."""
        # It might be not present when it is used without pytest via `APIOperation.as_strategy()`
        if self.test_function is not None:
            # Might be missing it in case of `LazySchema` usage
            return getattr(self.test_function, "_schemathesis_hooks", None)  # type: ignore
        return None

    def dispatch_hook(self, name: str, context: HookContext, *args: Any, **kwargs: Any) -> None:
        """Dispatch a hook via all available dispatchers."""
        dispatch(name, context, *args, **kwargs)
        self.hooks.dispatch(name, context, *args, **kwargs)
        local_dispatcher = self.get_local_hook_dispatcher()
        if local_dispatcher is not None:
            local_dispatcher.dispatch(name, context, *args, **kwargs)

    def prepare_multipart(
        self, form_data: FormData, operation: APIOperation
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
        path_parameters: PathParameters | None = None,
        headers: Headers | None = None,
        cookies: Cookies | None = None,
        query: Query | None = None,
        body: Body | NotSet = NOT_SET,
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

    def validate_response(self, operation: APIOperation, response: GenericResponse) -> bool | None:
        raise NotImplementedError

    def prepare_schema(self, schema: Any) -> Any:
        raise NotImplementedError

    def ratelimit(self) -> ContextManager:
        """Limit the rate of sending generated requests."""
        label = urlparse(self.base_url).netloc
        if self.rate_limiter is not None:
            if IS_PYRATE_LIMITER_ABOVE_3:
                self.rate_limiter.try_acquire(label)
            else:
                return self.rate_limiter.ratelimit(label, delay=True, max_delay=0)
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
        assert len(self) > 0, "No API operations found"
        strategies = [
            operation.ok().as_strategy(
                hooks=hooks,
                auth_storage=auth_storage,
                data_generation_method=data_generation_method,
                generation_config=generation_config,
                **kwargs,
            )
            for operation in self.get_all_operations(hooks=hooks)
            if isinstance(operation, Ok)
        ]
        return combine_strategies(strategies)


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
        assert len(self._data) > 0, "No API operations found"
        strategies = [
            operation.as_strategy(
                hooks=hooks,
                auth_storage=auth_storage,
                data_generation_method=data_generation_method,
                generation_config=generation_config,
                **kwargs,
            )
            for operation in self._data.values()
        ]
        return combine_strategies(strategies)
