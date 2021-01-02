"""Schema objects provide a convenient interface to raw schemas.

Their responsibilities:
  - Provide a unified way to work with different types of schemas
  - Give all endpoints / methods combinations that are available directly from the schema;

They give only static definitions of endpoints.
"""
from collections.abc import Mapping
from difflib import get_close_matches
from typing import Any, Callable, Dict, Generator, Iterable, Iterator, List, Optional, Sequence, Tuple, Type, Union
from urllib.parse import urljoin, urlsplit, urlunsplit

import attr
import hypothesis
from hypothesis.strategies import SearchStrategy
from hypothesis.utils.conventions import InferType
from requests.structures import CaseInsensitiveDict

from ._hypothesis import create_test
from .constants import DEFAULT_DATA_GENERATION_METHODS, DataGenerationMethod
from .hooks import HookContext, HookDispatcher, HookScope, dispatch
from .models import Case, Endpoint
from .stateful import APIStateMachine, Stateful, StatefulTest
from .types import Filter, FormData, GenericTest, NotSet
from .utils import NOT_SET, GenericResponse


class MethodsDict(CaseInsensitiveDict):
    def __getitem__(self, item: Any) -> Any:
        try:
            return super().__getitem__(item)
        except KeyError as exc:
            available_methods = ", ".join(map(str.upper, self))
            message = f"Method `{item}` not found. Available methods: {available_methods}"
            raise KeyError(message) from exc


@attr.s()  # pragma: no mutate
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
    test_function: Optional[GenericTest] = attr.ib(default=None)  # pragma: no mutate
    validate_schema: bool = attr.ib(default=True)  # pragma: no mutate
    skip_deprecated_endpoints: bool = attr.ib(default=False)  # pragma: no mutate
    data_generation_methods: Iterable[DataGenerationMethod] = attr.ib(
        default=DEFAULT_DATA_GENERATION_METHODS
    )  # pragma: no mutate

    def __iter__(self) -> Iterator[str]:
        return iter(self.endpoints)

    def __getitem__(self, item: str) -> MethodsDict:
        try:
            return self.endpoints[item]
        except KeyError as exc:
            matches = get_close_matches(item, list(self.endpoints))
            message = f"`{item}` not found"
            if matches:
                message += f". Did you mean `{matches[0]}`?"
            raise KeyError(message) from exc

    def __len__(self) -> int:
        return len(self.endpoints)

    @property  # pragma: no mutate
    def verbose_name(self) -> str:
        raise NotImplementedError

    def get_full_path(self, path: str) -> str:
        """Compute full path for the given path."""
        return urljoin(self.base_path, path.lstrip("/"))  # pragma: no mutate

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
    def endpoints(self) -> Dict[str, MethodsDict]:
        if not hasattr(self, "_endpoints"):
            # pylint: disable=attribute-defined-outside-init
            endpoints = self.get_all_endpoints()
            self._endpoints = endpoints_to_dict(endpoints)
        return self._endpoints

    @property
    def endpoints_count(self) -> int:
        total = 0
        # Avoid creating a list of all endpoints - for large schemas it consumes too much memory
        for _ in self.get_all_endpoints():
            total += 1
        return total

    def get_all_endpoints(self) -> Generator[Endpoint, None, None]:
        raise NotImplementedError

    def get_strategies_from_examples(self, endpoint: Endpoint) -> List[SearchStrategy[Case]]:
        """Get examples from the endpoint."""
        raise NotImplementedError

    def get_stateful_tests(
        self, response: GenericResponse, endpoint: Endpoint, stateful: Optional[Stateful]
    ) -> Sequence[StatefulTest]:
        """Get a list of additional tests, that should be executed after this response from the endpoint."""
        raise NotImplementedError

    def get_parameter_serializer(self, endpoint: Endpoint, location: str) -> Optional[Callable]:
        """Get a function that serializes parameters for the given location."""
        raise NotImplementedError

    def get_all_tests(
        self,
        func: Callable,
        settings: Optional[hypothesis.settings] = None,
        seed: Optional[int] = None,
    ) -> Generator[Tuple[Endpoint, DataGenerationMethod, Callable], None, None]:
        """Generate all endpoints and Hypothesis tests for them."""
        for endpoint in self.get_all_endpoints():
            for data_generation_method in self.data_generation_methods:
                test = create_test(
                    endpoint=endpoint,
                    test=func,
                    settings=settings,
                    seed=seed,
                    data_generation_method=data_generation_method,
                )
                yield endpoint, data_generation_method, test

    def parametrize(
        self,
        method: Optional[Filter] = NOT_SET,
        endpoint: Optional[Filter] = NOT_SET,
        tag: Optional[Filter] = NOT_SET,
        operation_id: Optional[Filter] = NOT_SET,
        validate_schema: Union[bool, NotSet] = NOT_SET,
        skip_deprecated_endpoints: Union[bool, NotSet] = NOT_SET,
        data_generation_methods: Union[Iterable[DataGenerationMethod], NotSet] = NOT_SET,
    ) -> Callable:
        """Mark a test function as a parametrized one."""

        def wrapper(func: GenericTest) -> GenericTest:
            HookDispatcher.add_dispatcher(func)
            func._schemathesis_test = self.clone(  # type: ignore
                test_function=func,
                method=method,
                endpoint=endpoint,
                tag=tag,
                operation_id=operation_id,
                validate_schema=validate_schema,
                skip_deprecated_endpoints=skip_deprecated_endpoints,
                data_generation_methods=data_generation_methods,
            )
            return func

        return wrapper

    def given(self, *args: Union[SearchStrategy, InferType], **kwargs: Union[SearchStrategy, InferType]) -> Callable:
        """Proxy Hypothesis strategies to ``hypothesis.given``."""

        def wrapper(func: GenericTest) -> GenericTest:
            func._schemathesis_given_args = args  # type: ignore
            func._schemathesis_given_kwargs = kwargs  # type: ignore
            return func

        return wrapper

    def clone(
        self,
        *,
        test_function: Optional[GenericTest] = None,
        method: Optional[Filter] = NOT_SET,
        endpoint: Optional[Filter] = NOT_SET,
        tag: Optional[Filter] = NOT_SET,
        operation_id: Optional[Filter] = NOT_SET,
        hooks: Union[HookDispatcher, NotSet] = NOT_SET,
        validate_schema: Union[bool, NotSet] = NOT_SET,
        skip_deprecated_endpoints: Union[bool, NotSet] = NOT_SET,
        data_generation_methods: Union[Iterable[DataGenerationMethod], NotSet] = NOT_SET,
    ) -> "BaseSchema":
        if method is NOT_SET:
            method = self.method
        if endpoint is NOT_SET:
            endpoint = self.endpoint
        if tag is NOT_SET:
            tag = self.tag
        if operation_id is NOT_SET:
            operation_id = self.operation_id
        if validate_schema is NOT_SET:
            validate_schema = self.validate_schema
        if skip_deprecated_endpoints is NOT_SET:
            skip_deprecated_endpoints = self.skip_deprecated_endpoints
        if hooks is NOT_SET:
            hooks = self.hooks
        if data_generation_methods is NOT_SET:
            data_generation_methods = self.data_generation_methods

        return self.__class__(
            self.raw_schema,
            location=self.location,
            base_url=self.base_url,
            method=method,
            endpoint=endpoint,
            tag=tag,
            operation_id=operation_id,
            app=self.app,
            hooks=hooks,  # type: ignore
            test_function=test_function,
            validate_schema=validate_schema,  # type: ignore
            skip_deprecated_endpoints=skip_deprecated_endpoints,  # type: ignore
            data_generation_methods=data_generation_methods,  # type: ignore
        )

    def get_local_hook_dispatcher(self) -> Optional[HookDispatcher]:
        """Get a HookDispatcher instance bound to the test if present."""
        # It might be not present when it is used without pytest via `Endpoint.as_strategy()`
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
        self, form_data: FormData, endpoint: Endpoint
    ) -> Tuple[Optional[List], Optional[Dict[str, Any]]]:
        """Split content of `form_data` into files & data.

        Forms may contain file fields, that we should send via `files` argument in `requests`.
        """
        raise NotImplementedError

    def get_request_payload_content_types(self, endpoint: Endpoint) -> List[str]:
        raise NotImplementedError

    def get_case_strategy(
        self,
        endpoint: Endpoint,
        hooks: Optional[HookDispatcher] = None,
        data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
    ) -> SearchStrategy:
        raise NotImplementedError

    def as_state_machine(self) -> Type[APIStateMachine]:
        raise NotImplementedError

    def get_links(self, endpoint: Endpoint) -> Dict[str, Dict[str, Any]]:
        raise NotImplementedError

    def validate_response(self, endpoint: Endpoint, response: GenericResponse) -> None:
        raise NotImplementedError

    def prepare_schema(self, schema: Any) -> Any:
        raise NotImplementedError


def endpoints_to_dict(endpoints: Generator[Endpoint, None, None]) -> Dict[str, MethodsDict]:
    output: Dict[str, MethodsDict] = {}
    for endpoint in endpoints:
        output.setdefault(endpoint.path, MethodsDict())
        output[endpoint.path][endpoint.method] = endpoint
    return output
