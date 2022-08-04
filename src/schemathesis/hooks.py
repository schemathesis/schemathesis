import inspect
from collections import defaultdict
from copy import deepcopy
from enum import Enum, unique
from typing import TYPE_CHECKING, Any, Callable, DefaultDict, Dict, List, Optional, Union, cast

import attr
from hypothesis import strategies as st

from .types import GenericTest
from .utils import GenericResponse, deprecated_property

if TYPE_CHECKING:
    from .models import APIOperation, Case
    from .schemas import BaseSchema


@unique
class HookScope(Enum):
    GLOBAL = 1
    SCHEMA = 2
    TEST = 3


@attr.s(slots=True)  # pragma: no mutate
class RegisteredHook:
    signature: inspect.Signature = attr.ib()  # pragma: no mutate
    scopes: List[HookScope] = attr.ib()  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class HookContext:
    """A context that is passed to some hook functions.

    :ivar Optional[APIOperation] operation: API operation that is currently being processed.
                                            Might be absent in some cases.
    """

    operation: Optional["APIOperation"] = attr.ib(default=None)  # pragma: no mutate

    @deprecated_property(removed_in="4.0", replacement="operation")
    def endpoint(self) -> Optional["APIOperation"]:
        return self.operation


@attr.s(slots=True)  # pragma: no mutate
class HookDispatcher:
    """Generic hook dispatcher.

    Provides a mechanism to extend Schemathesis in registered hook points.
    """

    scope: HookScope = attr.ib()  # pragma: no mutate
    _hooks: DefaultDict[str, List[Callable]] = attr.ib(factory=lambda: defaultdict(list))  # pragma: no mutate
    _specs: Dict[str, RegisteredHook] = {}  # pragma: no mutate

    def register(self, hook: Union[str, Callable]) -> Callable:
        """Register a new hook.

        :param hook: Either a hook function or a string.

        Can be used as a decorator in two forms.
        Without arguments for registering hooks and autodetecting their names:

        .. code-block:: python

            @schemathesis.hooks.register
            def before_generate_query(context, strategy):
                ...

        With a hook name as the first argument:

        .. code-block:: python

            @schemathesis.hooks.register("before_generate_query")
            def hook(context, strategy):
                ...
        """
        if isinstance(hook, str):

            def decorator(func: Callable) -> Callable:
                hook_name = cast(str, hook)
                return self.register_hook_with_name(func, hook_name)

            return decorator
        return self.register_hook_with_name(hook, hook.__name__)

    def merge(self, other: "HookDispatcher") -> "HookDispatcher":
        """Merge two dispatches together.

        The resulting dispatcher will call the `self` hooks first.
        """
        all_hooks = deepcopy(self._hooks)
        for name, hooks in other._hooks.items():
            all_hooks[name].extend(hooks)
        instance = self.__class__(scope=self.scope)
        instance._hooks = all_hooks
        return instance

    def apply(self, hook: Callable, *, name: Optional[str] = None) -> Callable[[Callable], Callable]:
        """Register hook to run only on one test function.

        :param hook: A hook function.
        :param Optional[str] name: A hook name.

        .. code-block:: python

            def before_generate_query(context, strategy):
                ...


            @schema.hooks.apply(before_generate_query)
            @schema.parametrize()
            def test_api(case):
                ...

        """

        if name is None:
            hook_name = hook.__name__
        else:
            hook_name = name

        def decorator(func: GenericTest) -> GenericTest:
            dispatcher = self.add_dispatcher(func)
            dispatcher.register_hook_with_name(hook, hook_name)
            return func

        return decorator

    @classmethod
    def add_dispatcher(cls, func: GenericTest) -> "HookDispatcher":
        """Attach a new dispatcher instance to the test if it is not already present."""
        if not hasattr(func, "_schemathesis_hooks"):
            func._schemathesis_hooks = cls(scope=HookScope.TEST)  # type: ignore
        return func._schemathesis_hooks  # type: ignore

    def register_hook_with_name(self, hook: Callable, name: str) -> Callable:
        """A helper for hooks registration."""
        self._validate_hook(name, hook)
        self._hooks[name].append(hook)
        return hook

    @classmethod
    def register_spec(cls, scopes: List[HookScope]) -> Callable:
        """Register hook specification.

        All hooks, registered with `register` should comply with corresponding registered specs.
        """

        def _register_spec(spec: Callable) -> Callable:
            cls._specs[spec.__name__] = RegisteredHook(inspect.signature(spec), scopes)
            return spec

        return _register_spec

    def _validate_hook(self, name: str, hook: Callable) -> None:
        """Basic validation for hooks being registered."""
        spec = self._specs.get(name)
        if spec is None:
            raise TypeError(f"There is no hook with name '{name}'")
        # Some hooks are not present on all levels. We need to avoid registering hooks on wrong levels.
        if self.scope not in spec.scopes:
            scopes = ", ".join(scope.name for scope in spec.scopes)
            raise ValueError(
                f"Cannot register hook '{name}' on {self.scope.name} scope dispatcher. "
                f"Use a dispatcher with {scopes} scope(s) instead"
            )
        signature = inspect.signature(hook)
        if len(signature.parameters) != len(spec.signature.parameters):
            raise TypeError(
                f"Hook '{name}' takes {len(spec.signature.parameters)} arguments but {len(signature.parameters)} is defined"
            )

    def collect_statistic(self) -> Dict[str, int]:
        return {name: len(hooks) for name, hooks in self._hooks.items()}

    def get_all_by_name(self, name: str) -> List[Callable]:
        """Get a list of hooks registered for a name."""
        return self._hooks.get(name, [])

    def dispatch(self, name: str, context: HookContext, *args: Any, **kwargs: Any) -> None:
        """Run all hooks for the given name."""
        for hook in self.get_all_by_name(name):
            hook(context, *args, **kwargs)

    def unregister(self, hook: Callable) -> None:
        """Unregister a specific hook.

        :param hook: A hook function to unregister.
        """
        # It removes this function from all places
        for hooks in self._hooks.values():
            hooks[:] = [item for item in hooks if item is not hook]

    def unregister_all(self) -> None:
        """Remove all registered hooks.

        Useful in tests.
        """
        self._hooks = defaultdict(list)


all_scopes = HookDispatcher.register_spec(list(HookScope))


@all_scopes
def before_generate_path_parameters(context: HookContext, strategy: st.SearchStrategy) -> st.SearchStrategy:
    """Called on a strategy that generates values for ``path_parameters``."""


@all_scopes
def before_generate_headers(context: HookContext, strategy: st.SearchStrategy) -> st.SearchStrategy:
    """Called on a strategy that generates values for ``headers``."""


@all_scopes
def before_generate_cookies(context: HookContext, strategy: st.SearchStrategy) -> st.SearchStrategy:
    """Called on a strategy that generates values for ``cookies``."""


@all_scopes
def before_generate_query(context: HookContext, strategy: st.SearchStrategy) -> st.SearchStrategy:
    """Called on a strategy that generates values for ``query``."""


@all_scopes
def before_generate_body(context: HookContext, strategy: st.SearchStrategy) -> st.SearchStrategy:
    """Called on a strategy that generates values for ``body``."""


@all_scopes
def before_generate_case(context: HookContext, strategy: st.SearchStrategy["Case"]) -> st.SearchStrategy["Case"]:
    """Called on a strategy that generates ``Case`` instances."""


@all_scopes
def before_process_path(context: HookContext, path: str, methods: Dict[str, Any]) -> None:
    """Called before API path is processed."""


@HookDispatcher.register_spec([HookScope.GLOBAL])
def before_load_schema(context: HookContext, raw_schema: Dict[str, Any]) -> None:
    """Called before schema instance is created."""


@HookDispatcher.register_spec([HookScope.GLOBAL])
def after_load_schema(context: HookContext, schema: "BaseSchema") -> None:
    """Called after schema instance is created."""


@all_scopes
def before_add_examples(context: HookContext, examples: List["Case"]) -> None:
    """Called before explicit examples are added to a test via `@example` decorator.

    `examples` is a list that could be extended with examples provided by the user.
    """


@all_scopes
def before_init_operation(context: HookContext, operation: "APIOperation") -> None:
    """Allows you to customize a newly created API operation."""


@HookDispatcher.register_spec([HookScope.GLOBAL])
def add_case(context: HookContext, case: "Case", response: GenericResponse) -> Optional["Case"]:
    """Creates an additional test per API operation. If this hook returns None, no additional test created.

    Called with a copy of the original case object and the server's response to the original case.
    """


@HookDispatcher.register_spec([HookScope.GLOBAL])
def before_call(context: HookContext, case: "Case") -> None:
    """Called before every network call in CLI tests.

    Use cases:
     - Modification of `case`. For example, adding some pre-determined value to its query string.
     - Logging
    """


@HookDispatcher.register_spec([HookScope.GLOBAL])
def after_call(context: HookContext, case: "Case", response: GenericResponse) -> None:
    """Called after every network call in CLI tests.

    Note that you need to modify the response in-place.

    Use cases:
     - Response post-processing, like modifying its payload.
     - Logging
    """


GLOBAL_HOOK_DISPATCHER = HookDispatcher(scope=HookScope.GLOBAL)
dispatch = GLOBAL_HOOK_DISPATCHER.dispatch
get_all_by_name = GLOBAL_HOOK_DISPATCHER.get_all_by_name
collect_statistic = GLOBAL_HOOK_DISPATCHER.collect_statistic
register = GLOBAL_HOOK_DISPATCHER.register
unregister = GLOBAL_HOOK_DISPATCHER.unregister
unregister_all = GLOBAL_HOOK_DISPATCHER.unregister_all
