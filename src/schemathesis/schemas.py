# pylint: disable=too-many-instance-attributes
"""Schema objects provide a convenient interface to raw schemas.

Their responsibilities:
  - Provide a unified way to work with different types of schemas
  - Give all endpoints / methods combinations that are available directly from the schema;

They give only static definitions of endpoints.
"""
from collections.abc import Mapping
from typing import Any, Callable, Dict, Generator, Iterator, List, Optional, Sequence, Tuple, Union

import attr
import hypothesis
from requests.structures import CaseInsensitiveDict

from ._hypothesis import make_test_or_exception
from .exceptions import InvalidSchema
from .hooks import HookContext, HookDispatcher, HookLocation, HookScope, dispatch, warn_deprecated_hook
from .models import Endpoint
from .stateful import StatefulTest
from .types import Filter, GenericTest, Hook, NotSet
from .utils import NOT_SET, GenericResponse, deprecated


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

    def __iter__(self) -> Iterator[str]:
        return iter(self.endpoints)

    def __getitem__(self, item: str) -> CaseInsensitiveDict:
        return self.endpoints[item]

    def __len__(self) -> int:
        return len(self.endpoints)

    @property  # pragma: no mutate
    def verbose_name(self) -> str:
        raise NotImplementedError

    @property
    def endpoints(self) -> Dict[str, CaseInsensitiveDict]:
        raise NotImplementedError

    @property
    def endpoints_count(self) -> int:
        return len(list(self.get_all_endpoints()))

    def get_all_endpoints(self) -> Generator[Endpoint, None, None]:
        raise NotImplementedError

    def get_stateful_tests(
        self, response: GenericResponse, endpoint: Endpoint, stateful: Optional[str]
    ) -> Sequence[StatefulTest]:
        """Get a list of additional tests, that should be executed after this response from the endpoint."""
        raise NotImplementedError

    def get_hypothesis_conversion(self, definitions: List[Dict[str, Any]]) -> Optional[Callable]:
        raise NotImplementedError

    def get_all_tests(
        self, func: Callable, settings: Optional[hypothesis.settings] = None, seed: Optional[int] = None
    ) -> Generator[Tuple[Endpoint, Union[Callable, InvalidSchema]], None, None]:
        """Generate all endpoints and Hypothesis tests for them."""
        test: Union[Callable, InvalidSchema]
        for endpoint in self.get_all_endpoints():
            test = make_test_or_exception(endpoint, func, settings, seed)
            yield endpoint, test

    def parametrize(  # pylint: disable=too-many-arguments
        self,
        method: Optional[Filter] = NOT_SET,
        endpoint: Optional[Filter] = NOT_SET,
        tag: Optional[Filter] = NOT_SET,
        operation_id: Optional[Filter] = NOT_SET,
        validate_schema: Union[bool, NotSet] = NOT_SET,
    ) -> Callable:
        """Mark a test function as a parametrized one."""

        def wrapper(func: GenericTest) -> GenericTest:
            HookDispatcher.add_dispatcher(func)
            func._schemathesis_test = self.clone(func, method, endpoint, tag, operation_id, validate_schema)  # type: ignore
            return func

        return wrapper

    def clone(  # pylint: disable=too-many-arguments
        self,
        test_function: Optional[GenericTest] = None,
        method: Optional[Filter] = NOT_SET,
        endpoint: Optional[Filter] = NOT_SET,
        tag: Optional[Filter] = NOT_SET,
        operation_id: Optional[Filter] = NOT_SET,
        validate_schema: Union[bool, NotSet] = NOT_SET,
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

        return self.__class__(
            self.raw_schema,
            location=self.location,
            base_url=self.base_url,
            method=method,
            endpoint=endpoint,
            tag=tag,
            operation_id=operation_id,
            app=self.app,
            hooks=self.hooks,
            test_function=test_function,
            validate_schema=validate_schema,  # type: ignore
        )

    @deprecated("'register_hook` is deprecated, use `hooks.register' instead")
    def register_hook(self, place: str, hook: Hook) -> None:
        warn_deprecated_hook(hook)
        if place not in HookLocation.__members__:
            raise KeyError(place)
        self.hooks.register_hook_with_name(f"before_generate_{place}", hook, skip_validation=True)

    @deprecated("'with_hook` is deprecated, use `hooks.apply' instead")
    def with_hook(self, place: str, hook: Hook) -> Callable[[GenericTest], GenericTest]:
        """Register a hook for a specific test."""
        warn_deprecated_hook(hook)
        if place not in HookLocation.__members__:
            raise KeyError(place)

        return self.hooks.apply(f"before_generate_{place}", hook, skip_validation=True)

    def get_local_hook_dispatcher(self) -> Optional[HookDispatcher]:
        """Get a HookDispatcher instance bound to the test if present."""
        # It might be not present when it is used without pytest via `Endpoint.as_strategy()`
        if self.test_function is not None:
            return self.test_function._schemathesis_hooks  # type: ignore
        return None

    def dispatch_hook(self, name: str, context: HookContext, *args: Any, **kwargs: Any) -> None:
        """Dispatch a hook via all available dispatchers."""
        dispatch(name, context, *args, **kwargs)
        self.hooks.dispatch(name, context, *args, **kwargs)
        local_dispatcher = self.get_local_hook_dispatcher()
        if local_dispatcher is not None:
            local_dispatcher.dispatch(name, context, *args, **kwargs)
