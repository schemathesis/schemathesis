"""Schema objects provide a convenient interface to raw schemas.

Their responsibilities:
  - Provide a unified way to work with different types of schemas
  - Give all paths / methods combinations that are available directly from the schema;

They give only static definitions of paths.
"""
from collections.abc import Mapping
from difflib import get_close_matches
from functools import lru_cache
from typing import (
    Any,
    Callable,
    Dict,
    Generator,
    Iterable,
    Iterator,
    List,
    NoReturn,
    Optional,
    Sequence,
    Tuple,
    Type,
    TypeVar,
    Union,
)
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import attr
import hypothesis
from hypothesis.strategies import SearchStrategy
from requests.structures import CaseInsensitiveDict

from ._hypothesis import create_test
from .auth import AuthStorage
from .constants import DEFAULT_DATA_GENERATION_METHODS, CodeSampleStyle, DataGenerationMethod
from .exceptions import InvalidSchema, UsageError
from .hooks import HookContext, HookDispatcher, HookScope, dispatch
from .models import APIOperation, Case
from .stateful import APIStateMachine, Stateful, StatefulTest
from .types import (
    Body,
    Cookies,
    DataGenerationMethodInput,
    Filter,
    FormData,
    GenericTest,
    Headers,
    NotSet,
    PathParameters,
    Query,
)
from .utils import NOT_SET, PARAMETRIZE_MARKER, GenericResponse, GivenInput, Ok, Result, given_proxy


class MethodsDict(CaseInsensitiveDict):
    """Container for accessing API operations.

    Provides a more specific error message if API operation is not found.
    """

    def __getitem__(self, item: Any) -> Any:
        try:
            return super().__getitem__(item)
        except KeyError as exc:
            available_methods = ", ".join(map(str.upper, self))
            message = f"Method `{item}` not found. Available methods: {available_methods}"
            raise KeyError(message) from exc


C = TypeVar("C", bound=Case)


@lru_cache()
def get_full_path(base_path: str, path: str) -> str:
    return unquote(urljoin(base_path, quote(path.lstrip("/"))))  # pragma: no mutate


@attr.s(eq=False)  # pragma: no mutate
class BaseSchema(Mapping):
    raw_schema: Dict[str, Any] = attr.ib()  # pragma: no mutate
    location: Optional[str] = attr.ib(default=None)  # pragma: no mutate
    base_url: Optional[str] = attr.ib(default=None)  # pragma: no mutate
    method: Optional[Filter] = attr.ib(default=None)  # pragma: no mutate
    endpoint: Optional[Filter] = attr.ib(default=None)  # pragma: no mutate
    tag: Optional[Filter] = attr.ib(default=None)  # pragma: no mutate
    operation_id: Optional[Filter] = attr.ib(default=None)  # pragma: no mutate
    app: Any = attr.ib(default=None)  # pragma: no mutate
    hooks: HookDispatcher = attr.ib(factory=lambda: HookDispatcher(scope=HookScope.SCHEMA))  # pragma: no mutate
    auth: AuthStorage = attr.ib(factory=AuthStorage)  # pragma: no mutate
    test_function: Optional[GenericTest] = attr.ib(default=None)  # pragma: no mutate
    validate_schema: bool = attr.ib(default=True)  # pragma: no mutate
    skip_deprecated_operations: bool = attr.ib(default=False)  # pragma: no mutate
    data_generation_methods: List[DataGenerationMethod] = attr.ib(
        default=DEFAULT_DATA_GENERATION_METHODS
    )  # pragma: no mutate
    code_sample_style: CodeSampleStyle = attr.ib(default=CodeSampleStyle.default())  # pragma: no mutate

    def __iter__(self) -> Iterator[str]:
        return iter(self.operations)

    def __getitem__(self, item: str) -> MethodsDict:
        try:
            return self.operations[item]
        except KeyError as exc:
            matches = get_close_matches(item, list(self.operations))
            message = f"`{item}` not found"
            if matches:
                message += f". Did you mean `{matches[0]}`?"
            raise KeyError(message) from exc

    def __len__(self) -> int:
        return len(self.operations)

    @property  # pragma: no mutate
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
            return base_url.rstrip("/")  # pragma: no mutate
        return self._build_base_url()

    @property
    def operations(self) -> Dict[str, MethodsDict]:
        if not hasattr(self, "_operations"):
            # pylint: disable=attribute-defined-outside-init
            operations = self.get_all_operations()
            self._operations = operations_to_dict(operations)
        return self._operations

    @property
    def operations_count(self) -> int:
        raise NotImplementedError

    def get_all_operations(self) -> Generator[Result[APIOperation, InvalidSchema], None, None]:
        raise NotImplementedError

    def get_strategies_from_examples(self, operation: APIOperation) -> List[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        raise NotImplementedError

    def get_security_requirements(self, operation: APIOperation) -> List[str]:
        """Get applied security requirements for the given API operation."""
        raise NotImplementedError

    def get_stateful_tests(
        self, response: GenericResponse, operation: APIOperation, stateful: Optional[Stateful]
    ) -> Sequence[StatefulTest]:
        """Get a list of additional tests, that should be executed after this response from the API operation."""
        raise NotImplementedError

    def get_parameter_serializer(self, operation: APIOperation, location: str) -> Optional[Callable]:
        """Get a function that serializes parameters for the given location."""
        raise NotImplementedError

    def get_all_tests(
        self,
        func: Callable,
        settings: Optional[hypothesis.settings] = None,
        seed: Optional[int] = None,
        _given_kwargs: Optional[Dict[str, GivenInput]] = None,
    ) -> Generator[Result[Tuple[APIOperation, Callable], InvalidSchema], None, None]:
        """Generate all operations and Hypothesis tests for them."""
        for result in self.get_all_operations():
            if isinstance(result, Ok):
                test = create_test(
                    operation=result.ok(),
                    test=func,
                    settings=settings,
                    seed=seed,
                    data_generation_methods=self.data_generation_methods,
                    _given_kwargs=_given_kwargs,
                )
                yield Ok((result.ok(), test))
            else:
                yield result

    def parametrize(
        self,
        method: Optional[Filter] = NOT_SET,
        endpoint: Optional[Filter] = NOT_SET,
        tag: Optional[Filter] = NOT_SET,
        operation_id: Optional[Filter] = NOT_SET,
        validate_schema: Union[bool, NotSet] = NOT_SET,
        skip_deprecated_operations: Union[bool, NotSet] = NOT_SET,
        data_generation_methods: Union[Iterable[DataGenerationMethod], NotSet] = NOT_SET,
        code_sample_style: Union[str, NotSet] = NOT_SET,
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
        base_url: Union[Optional[str], NotSet] = NOT_SET,
        test_function: Optional[GenericTest] = None,
        method: Optional[Filter] = NOT_SET,
        endpoint: Optional[Filter] = NOT_SET,
        tag: Optional[Filter] = NOT_SET,
        operation_id: Optional[Filter] = NOT_SET,
        app: Any = NOT_SET,
        hooks: Union[HookDispatcher, NotSet] = NOT_SET,
        auth: Union[AuthStorage, NotSet] = NOT_SET,
        validate_schema: Union[bool, NotSet] = NOT_SET,
        skip_deprecated_operations: Union[bool, NotSet] = NOT_SET,
        data_generation_methods: Union[DataGenerationMethodInput, NotSet] = NOT_SET,
        code_sample_style: Union[CodeSampleStyle, NotSet] = NOT_SET,
    ) -> "BaseSchema":
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
        if code_sample_style is NOT_SET:
            code_sample_style = self.code_sample_style

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
            code_sample_style=code_sample_style,  # type: ignore
        )

    def get_local_hook_dispatcher(self) -> Optional[HookDispatcher]:
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
    ) -> Tuple[Optional[List], Optional[Dict[str, Any]]]:
        """Split content of `form_data` into files & data.

        Forms may contain file fields, that we should send via `files` argument in `requests`.
        """
        raise NotImplementedError

    def get_request_payload_content_types(self, operation: APIOperation) -> List[str]:
        raise NotImplementedError

    def make_case(
        self,
        *,
        case_cls: Type[C],
        operation: APIOperation,
        path_parameters: Optional[PathParameters] = None,
        headers: Optional[Headers] = None,
        cookies: Optional[Cookies] = None,
        query: Optional[Query] = None,
        body: Union[Body, NotSet] = NOT_SET,
        media_type: Optional[str] = None,
    ) -> C:
        raise NotImplementedError

    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: Optional[HookDispatcher] = None,
        auth_storage: Optional[AuthStorage] = None,
        data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
    ) -> SearchStrategy:
        raise NotImplementedError

    def as_state_machine(self) -> Type[APIStateMachine]:
        """Create a state machine class.

        Use it for stateful testing.
        """
        raise NotImplementedError

    def get_links(self, operation: APIOperation) -> Dict[str, Dict[str, Any]]:
        raise NotImplementedError

    def validate_response(self, operation: APIOperation, response: GenericResponse) -> None:
        raise NotImplementedError

    def prepare_schema(self, schema: Any) -> Any:
        raise NotImplementedError


def operations_to_dict(
    operations: Generator[Result[APIOperation, InvalidSchema], None, None]
) -> Dict[str, MethodsDict]:
    output: Dict[str, MethodsDict] = {}
    for result in operations:
        if isinstance(result, Ok):
            operation = result.ok()
            output.setdefault(operation.path, MethodsDict())
            output[operation.path][operation.method] = operation
    return output
