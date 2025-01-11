from __future__ import annotations

import inspect
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, unique
from functools import partial
from typing import TYPE_CHECKING, Any, Callable, ClassVar, cast

from schemathesis.core.marks import Mark
from schemathesis.core.transport import Response
from schemathesis.filters import FilterSet, attach_filter_chain

if TYPE_CHECKING:
    from hypothesis import strategies as st

    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation, BaseSchema

HookDispatcherMark = Mark["HookDispatcher"](attr_name="hook_dispatcher")


@unique
class HookScope(Enum):
    GLOBAL = 1
    SCHEMA = 2
    TEST = 3


@dataclass
class RegisteredHook:
    signature: inspect.Signature
    scopes: list[HookScope]

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...


@dataclass
class HookContext:
    """A context that is passed to some hook functions.

    :ivar Optional[APIOperation] operation: API operation that is currently being processed.
                                            Might be absent in some cases.
    """

    operation: APIOperation | None = None


def to_filterable_hook(dispatcher: HookDispatcher) -> Callable:
    filter_used = False
    filter_set = FilterSet()

    def register(hook: str | Callable) -> Callable:
        nonlocal filter_set

        if filter_used:
            validate_filterable_hook(hook)

        if isinstance(hook, str):

            def decorator(func: Callable) -> Callable:
                hook_name = cast(str, hook)
                if filter_used:
                    validate_filterable_hook(hook)
                func.filter_set = filter_set  # type: ignore[attr-defined]
                return dispatcher.register_hook_with_name(func, hook_name)

            init_filter_set(decorator)
            return decorator

        hook.filter_set = filter_set  # type: ignore[attr-defined]
        init_filter_set(register)
        return dispatcher.register_hook_with_name(hook, hook.__name__)

    def init_filter_set(target: Callable) -> FilterSet:
        nonlocal filter_used

        filter_used = False
        filter_set = FilterSet()

        def include(*args: Any, **kwargs: Any) -> None:
            nonlocal filter_used

            filter_used = True
            filter_set.include(*args, **kwargs)

        def exclude(*args: Any, **kwargs: Any) -> None:
            nonlocal filter_used

            filter_used = True
            filter_set.exclude(*args, **kwargs)

        attach_filter_chain(target, "apply_to", include)
        attach_filter_chain(target, "skip_for", exclude)
        return filter_set

    filter_set = init_filter_set(register)
    return register


@dataclass
class HookDispatcher:
    """Generic hook dispatcher.

    Provides a mechanism to extend Schemathesis in registered hook points.
    """

    scope: HookScope
    _hooks: defaultdict[str, list[Callable]] = field(default_factory=lambda: defaultdict(list))
    _specs: ClassVar[dict[str, RegisteredHook]] = {}

    def __post_init__(self) -> None:
        self.register = to_filterable_hook(self)  # type: ignore[method-assign]

    def register(self, hook: str | Callable) -> Callable:
        """Register a new hook.

        :param hook: Either a hook function or a string.

        Can be used as a decorator in two forms.
        Without arguments for registering hooks and autodetecting their names:

        .. code-block:: python

            @schemathesis.hook
            def before_generate_query(context, strategy):
                ...

        With a hook name as the first argument:

        .. code-block:: python

            @schemathesis.hook("before_generate_query")
            def hook(context, strategy):
                ...
        """
        raise NotImplementedError

    def apply(self, hook: Callable, *, name: str | None = None) -> Callable[[Callable], Callable]:
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

        def decorator(func: Callable) -> Callable:
            dispatcher = self.add_dispatcher(func)
            dispatcher.register_hook_with_name(hook, hook_name)
            return func

        return decorator

    @classmethod
    def add_dispatcher(cls, func: Callable) -> HookDispatcher:
        """Attach a new dispatcher instance to the test if it is not already present."""
        if not HookDispatcherMark.is_set(func):
            HookDispatcherMark.set(func, cls(scope=HookScope.TEST))
        dispatcher = HookDispatcherMark.get(func)
        assert dispatcher is not None
        return dispatcher

    def register_hook_with_name(self, hook: Callable, name: str) -> Callable:
        """A helper for hooks registration."""
        self._validate_hook(name, hook)
        self._hooks[name].append(hook)
        return hook

    @classmethod
    def register_spec(cls, scopes: list[HookScope]) -> Callable:
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

    def get_all_by_name(self, name: str) -> list[Callable]:
        """Get a list of hooks registered for a name."""
        return self._hooks.get(name, [])

    def apply_to_container(
        self, strategy: st.SearchStrategy, container: str, context: HookContext
    ) -> st.SearchStrategy:
        for hook in self.get_all_by_name(f"before_generate_{container}"):
            if _should_skip_hook(hook, context):
                continue
            strategy = hook(context, strategy)
        for hook in self.get_all_by_name(f"filter_{container}"):
            if _should_skip_hook(hook, context):
                continue
            hook = partial(hook, context)
            strategy = strategy.filter(hook)
        for hook in self.get_all_by_name(f"map_{container}"):
            if _should_skip_hook(hook, context):
                continue
            hook = partial(hook, context)
            strategy = strategy.map(hook)
        for hook in self.get_all_by_name(f"flatmap_{container}"):
            if _should_skip_hook(hook, context):
                continue
            hook = partial(hook, context)
            strategy = strategy.flatmap(hook)
        return strategy

    def dispatch(self, name: str, context: HookContext, *args: Any, **kwargs: Any) -> None:
        """Run all hooks for the given name."""
        for hook in self.get_all_by_name(name):
            if _should_skip_hook(hook, context):
                continue
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


def _should_skip_hook(hook: Callable, ctx: HookContext) -> bool:
    filter_set = getattr(hook, "filter_set", None)
    return filter_set is not None and ctx.operation is not None and not filter_set.match(ctx)


def apply_to_all_dispatchers(
    operation: APIOperation,
    context: HookContext,
    hooks: HookDispatcher | None,
    strategy: st.SearchStrategy,
    container: str,
) -> st.SearchStrategy:
    """Apply all hooks related to the given location."""
    strategy = GLOBAL_HOOK_DISPATCHER.apply_to_container(strategy, container, context)
    strategy = operation.schema.hooks.apply_to_container(strategy, container, context)
    if hooks is not None:
        strategy = hooks.apply_to_container(strategy, container, context)
    return strategy


def validate_filterable_hook(hook: str | Callable) -> None:
    if callable(hook):
        name = hook.__name__
    else:
        name = hook
    if name in ("before_process_path", "before_load_schema", "after_load_schema"):
        raise ValueError(f"Filters are not applicable to this hook: `{name}`")


all_scopes = HookDispatcher.register_spec(list(HookScope))


for action in ("filter", "map", "flatmap"):
    for target in ("path_parameters", "query", "headers", "cookies", "body", "case"):
        exec(
            f"""
@all_scopes
def {action}_{target}(context: HookContext, {target}: Any) -> Any:
    pass
""",
            globals(),
        )


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
def before_generate_case(context: HookContext, strategy: st.SearchStrategy[Case]) -> st.SearchStrategy[Case]:
    """Called on a strategy that generates ``Case`` instances."""


@all_scopes
def before_process_path(context: HookContext, path: str, methods: dict[str, Any]) -> None:
    """Called before API path is processed."""


@HookDispatcher.register_spec([HookScope.GLOBAL])
def before_load_schema(context: HookContext, raw_schema: dict[str, Any]) -> None:
    """Called before schema instance is created."""


@HookDispatcher.register_spec([HookScope.GLOBAL])
def after_load_schema(context: HookContext, schema: BaseSchema) -> None:
    """Called after schema instance is created."""


@all_scopes
def before_add_examples(context: HookContext, examples: list[Case]) -> None:
    """Called before explicit examples are added to a test via `@example` decorator.

    `examples` is a list that could be extended with examples provided by the user.
    """


@all_scopes
def before_init_operation(context: HookContext, operation: APIOperation) -> None:
    """Allows you to customize a newly created API operation."""


@HookDispatcher.register_spec([HookScope.GLOBAL])
def before_call(context: HookContext, case: Case, **kwargs: Any) -> None:
    """Called before every network call in CLI tests.

    Use cases:
     - Modification of `case`. For example, adding some pre-determined value to its query string.
     - Logging
    """


@HookDispatcher.register_spec([HookScope.GLOBAL])
def after_call(context: HookContext, case: Case, response: Response) -> None:
    """Called after every network call in CLI tests.

    Note that you need to modify the response in-place.

    Use cases:
     - Response post-processing, like modifying its payload.
     - Logging
    """


GLOBAL_HOOK_DISPATCHER = HookDispatcher(scope=HookScope.GLOBAL)
dispatch = GLOBAL_HOOK_DISPATCHER.dispatch
get_all_by_name = GLOBAL_HOOK_DISPATCHER.get_all_by_name
register = GLOBAL_HOOK_DISPATCHER.register
unregister = GLOBAL_HOOK_DISPATCHER.unregister
unregister_all = GLOBAL_HOOK_DISPATCHER.unregister_all
