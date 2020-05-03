import inspect
import warnings
from collections import defaultdict
from typing import Any, Callable, DefaultDict, Dict, List, Optional, Union, cast

import attr
from hypothesis import strategies as st

from .constants import HookLocation
from .models import Endpoint
from .types import GenericTest, Hook


def warn_deprecated_hook(hook: Hook) -> None:
    if "context" not in inspect.signature(hook).parameters:
        warnings.warn(
            DeprecationWarning(
                "Hook functions that do not accept `context` argument are deprecated and "
                "support will be removed in Schemathesis 2.0."
            )
        )


@attr.s(slots=True)  # pragma: no mutate
class HookContext:
    """A context that is passed to some hook functions."""

    endpoint: Optional[Endpoint] = attr.ib(default=None)  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class HookDispatcher:
    """Generic hook dispatcher.

    Provides a mechanism to extend Schemathesis in registered hook points.
    """

    _hooks: DefaultDict[str, List[Callable]] = attr.ib(factory=lambda: defaultdict(list))  # pragma: no mutate
    _specs: Dict[str, inspect.Signature] = {}  # pragma: no mutate

    def register(self, hook: Union[str, Callable]) -> Callable:
        """Register a new hook.

        Can be used as a decorator in two forms.
        Without arguments for registering hooks and autodetecting their names:

            @schema.hooks.register
            def before_generate_query(strategy, context):
                ...

        With a hook name as the first argument:

            @schema.hooks.register("before_generate_query")
            def hook(strategy, context):
                ...
        """
        if isinstance(hook, str):

            def decorator(func: Callable) -> Callable:
                hook_name = cast(str, hook)
                return self.register_hook_with_name(hook_name, func)

            return decorator
        return self.register_hook_with_name(hook.__name__, hook)

    def apply(self, name: str, hook: Callable, skip_validation: bool = False) -> Callable[[Callable], Callable]:
        """Register hook to run only on one test function.

        Example:
            def hook(strategy, context):
                ...

            @schema.hooks.apply("before_generate_query", hook)
            @schema.parametrize()
            def test_api(case):
                ...

        """

        def decorator(func: GenericTest) -> GenericTest:
            dispatcher = self.add_dispatcher(func)
            dispatcher.register_hook_with_name(name, hook, skip_validation)
            return func

        return decorator

    @classmethod
    def add_dispatcher(cls, func: GenericTest) -> "HookDispatcher":
        """Attach a new dispatcher instance to the test if it is not already present."""
        if not hasattr(func, "_schemathesis_hooks"):
            func._schemathesis_hooks = cls()  # type: ignore
        return func._schemathesis_hooks  # type: ignore

    def register_hook_with_name(self, name: str, hook: Callable, skip_validation: bool = False) -> Callable:
        """A helper for hooks registration.

        Besides its use in this class internally it is used to keep backward compatibility with the old hooks system.
        """
        # Validation is skipped only for backward compatibility with the old hooks system
        if not skip_validation:
            self._validate_hook(name, hook)
        self._hooks[name].append(hook)
        return hook

    @classmethod
    def register_spec(cls, spec: Callable) -> Callable:
        """Register hook specification.

        All hooks, registered with `register` should comply with corresponding registered specs.
        """
        cls._specs[spec.__name__] = inspect.signature(spec)
        return spec

    def _validate_hook(self, name: str, hook: Callable) -> None:
        """Basic validation for hooks being registered."""
        spec = self._specs.get(name)
        if spec is None:
            raise TypeError(f"There is no hook with name '{name}'")
        signature = inspect.signature(hook)
        if len(signature.parameters) != len(spec.parameters):
            raise TypeError(
                f"Hook '{name}' takes {len(spec.parameters)} arguments but {len(signature.parameters)} is defined"
            )

    def get_hooks(self, name: str) -> List[Callable]:
        """Get a list of hooks registered for name."""
        return self._hooks.get(name, [])

    def dispatch(self, name: str, context: HookContext, *args: Any, **kwargs: Any) -> None:
        """Run all hooks for the given name."""
        for hook in self.get_hooks(name):
            hook(context, *args, **kwargs)

    def unregister(self, hook: Callable) -> None:
        """Unregister a specific hook."""
        # It removes this function from all places
        for hooks in self._hooks.values():
            hooks[:] = [item for item in hooks if item is not hook]

    def unregister_all(self) -> None:
        """Remove all registered hooks.

        Useful in tests.
        """
        self._hooks = defaultdict(list)


@HookDispatcher.register_spec
def before_generate_path_parameters(strategy: st.SearchStrategy, context: HookContext) -> st.SearchStrategy:
    pass


@HookDispatcher.register_spec
def before_generate_headers(strategy: st.SearchStrategy, context: HookContext) -> st.SearchStrategy:
    pass


@HookDispatcher.register_spec
def before_generate_cookies(strategy: st.SearchStrategy, context: HookContext) -> st.SearchStrategy:
    pass


@HookDispatcher.register_spec
def before_generate_query(strategy: st.SearchStrategy, context: HookContext) -> st.SearchStrategy:
    pass


@HookDispatcher.register_spec
def before_generate_body(strategy: st.SearchStrategy, context: HookContext) -> st.SearchStrategy:
    pass


@HookDispatcher.register_spec
def before_generate_form_data(strategy: st.SearchStrategy, context: HookContext) -> st.SearchStrategy:
    pass


@HookDispatcher.register_spec
def before_process_path(context: HookContext, path: str, methods: Dict[str, Any]) -> None:
    pass


@HookDispatcher.register_spec
def before_load_schema(context: HookContext, raw_schema: Dict[str, Any]) -> None:
    pass


GLOBAL_HOOK_DISPATCHER = HookDispatcher()
dispatch = GLOBAL_HOOK_DISPATCHER.dispatch
get_hooks = GLOBAL_HOOK_DISPATCHER.get_hooks
unregister = GLOBAL_HOOK_DISPATCHER.unregister
unregister_all = GLOBAL_HOOK_DISPATCHER.unregister_all


def register(*args: Union[str, Callable]) -> Callable:
    # This code suppose to support backward compatibility with the old hook system.
    # In Schemathesis 2.0 this function can be replaced with `register = GLOBAL_HOOK_DISPATCHER.register`
    if len(args) == 1:
        return GLOBAL_HOOK_DISPATCHER.register(args[0])
    if len(args) == 2:
        warnings.warn(
            "Calling `schemathesis.register` with two arguments is deprecated, use it as a decorator instead.",
            DeprecationWarning,
        )
        place, hook = args
        hook = cast(Callable, hook)
        warn_deprecated_hook(hook)
        if place not in HookLocation.__members__:
            raise KeyError(place)
        return GLOBAL_HOOK_DISPATCHER.register_hook_with_name(f"before_generate_{place}", hook, skip_validation=True)
    # This approach is quite naive, but it should be enough for the common use case
    raise TypeError("Invalid number of arguments. Please, use `register` as a decorator.")
