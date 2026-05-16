from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum, unique
from functools import lru_cache, partial
from typing import TYPE_CHECKING, Any, ClassVar, cast

from schemathesis.core.errors import HookExecutionError
from schemathesis.core.jsonschema.types import JsonSchemaObject
from schemathesis.core.marks import Mark
from schemathesis.core.transport import Response
from schemathesis.filters import FilterSet, attach_filter_chain

if TYPE_CHECKING:
    import requests
    from hypothesis import strategies as st

    from schemathesis.checks import CheckResult
    from schemathesis.core.parameters import ContainerName
    from schemathesis.core.spec import SchemaMetadata
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation, BaseSchema


HookDispatcherMark = Mark["HookDispatcher"](attr_name="hook_dispatcher")


@unique
class HookScope(int, Enum):
    GLOBAL = 1
    SCHEMA = 2
    TEST = 3


@dataclass(slots=True)
class RegisteredHook:
    signature: inspect.Signature
    scopes: list[HookScope]

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...


@dataclass(slots=True)
class HookContext:
    """A context that is passed to some hook functions."""

    operation: APIOperation | None
    """API operation that is currently being processed."""

    def __init__(self, *, operation: APIOperation | None = None) -> None:
        self.operation = operation


def to_filterable_hook(dispatcher: HookDispatcher) -> Callable:
    filter_used = False
    filter_set = FilterSet()

    @contextmanager
    def _reset_on_error() -> Generator:
        try:
            yield
        except Exception:
            filter_set.clear()
            raise

    def register(hook: str | Callable) -> Callable:
        nonlocal filter_set

        if filter_used:
            with _reset_on_error():
                validate_filterable_hook(hook)

        if isinstance(hook, str):

            def decorator(func: Callable) -> Callable:
                hook_name = cast(str, hook)
                if filter_used:
                    with _reset_on_error():
                        validate_filterable_hook(hook)
                func.filter_set = filter_set  # type: ignore[attr-defined]
                return dispatcher.register_hook_with_name(func, hook_name)

            init_filter_set(decorator)
            return decorator

        hook.filter_set = filter_set  # type: ignore[attr-defined]
        filter_set = init_filter_set(register)
        return dispatcher.register_hook_with_name(hook, hook.__name__)

    def init_filter_set(target: Callable) -> FilterSet:
        nonlocal filter_used

        filter_used = False
        filter_set = FilterSet()

        def include(*args: Any, **kwargs: Any) -> None:
            nonlocal filter_used

            filter_used = True
            with _reset_on_error():
                filter_set.include(*args, **kwargs)

        def exclude(*args: Any, **kwargs: Any) -> None:
            nonlocal filter_used

            filter_used = True
            with _reset_on_error():
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

    @property
    def hook(self) -> Callable:
        return to_filterable_hook(self)

    def apply(self, hook: Callable, *, name: str | None = None) -> Callable[[Callable], Callable]:
        """Register hook to run only on one test function.

        Args:
            hook: A hook function.
            name: A hook name.

        Example:
            ```python
            def filter_query(ctx, value):
                ...


            @schema.hooks.apply(filter_query)
            @schema.parametrize()
            def test_api(case):
                ...
            ```

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

    def defines(self, name: str) -> bool:
        """Return True if any hooks are registered under the given name."""
        return bool(self._hooks.get(name))

    def get_all(self) -> dict[str, list[Callable]]:
        return self._hooks

    def apply_to_container(
        self,
        strategy: st.SearchStrategy,
        container: ContainerName,
        context: HookContext,
        *,
        filter_wrapper: Callable[[Callable], Callable] | None = None,
        map_wrapper: Callable[[Callable], Callable] | None = None,
        flatmap_wrapper: Callable[[Callable], Callable] | None = None,
    ) -> st.SearchStrategy:
        for hook in self.get_all_by_name(f"before_generate_{container}"):
            if _should_skip_hook(hook, context):
                continue
            strategy = hook(context, strategy)
        for hook in self.get_all_by_name(f"filter_{container}"):
            if _should_skip_hook(hook, context):
                continue
            hook = partial(hook, context)
            if filter_wrapper is not None:
                hook = filter_wrapper(hook)
            strategy = strategy.filter(hook)
        for hook in self.get_all_by_name(f"map_{container}"):
            if _should_skip_hook(hook, context):
                continue
            hook = partial(hook, context)
            if map_wrapper is not None:
                hook = map_wrapper(hook)
            strategy = strategy.map(hook)
        for hook in self.get_all_by_name(f"flatmap_{container}"):
            if _should_skip_hook(hook, context):
                continue
            hook = partial(hook, context)
            if flatmap_wrapper is not None:
                hook = flatmap_wrapper(hook)
            strategy = strategy.flatmap(hook)
        return strategy

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


@lru_cache(maxsize=16)
def has_var_keyword(hook: Callable) -> bool:
    """Check if hook function accepts **kwargs."""
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in inspect.signature(hook).parameters.values())


def _should_skip_hook(hook: Callable, ctx: HookContext) -> bool:
    filter_set = getattr(hook, "filter_set", None)
    return filter_set is not None and ctx.operation is not None and not filter_set.match(ctx)


def _dispatch_to_all(
    name: str,
    dispatchers: tuple[HookDispatcher, ...],
    context: HookContext,
    *args: Any,
) -> None:
    for dispatcher in dispatchers:
        for hook in dispatcher.get_all_by_name(name):
            if _should_skip_hook(hook, context):
                continue
            try:
                hook(context, *args)
            except Exception as exc:
                raise HookExecutionError(name, exc) from exc


def apply_to_all_dispatchers(
    operation: APIOperation,
    context: HookContext,
    hooks: HookDispatcher | None,
    strategy: st.SearchStrategy,
    container: ContainerName,
    *,
    filter_wrapper: Callable[[Callable], Callable] | None = None,
    map_wrapper: Callable[[Callable], Callable] | None = None,
    flatmap_wrapper: Callable[[Callable], Callable] | None = None,
) -> st.SearchStrategy:
    """Apply all hooks related to the given location."""
    wrappers = {
        "filter_wrapper": filter_wrapper,
        "map_wrapper": map_wrapper,
        "flatmap_wrapper": flatmap_wrapper,
    }
    strategy = GLOBAL_HOOK_DISPATCHER.apply_to_container(strategy, container, context, **wrappers)
    strategy = operation.schema.hooks.apply_to_container(strategy, container, context, **wrappers)
    if hooks is not None:
        strategy = hooks.apply_to_container(strategy, container, context, **wrappers)
    return strategy


def validate_filterable_hook(hook: str | Callable) -> None:
    if callable(hook):
        name = hook.__name__
    else:
        name = hook
    if name in ("before_process_path", "before_load_schema", "after_load_schema"):
        raise ValueError(f"Filters are not applicable to this hook: `{name}`")


# Hook spec definitions live in `hook_specs`; importing the module triggers registration.
from schemathesis import hook_specs  # noqa: E402, F401

GLOBAL_HOOK_DISPATCHER = HookDispatcher(scope=HookScope.GLOBAL)
get_all_by_name = GLOBAL_HOOK_DISPATCHER.get_all_by_name
defines = GLOBAL_HOOK_DISPATCHER.defines
unregister = GLOBAL_HOOK_DISPATCHER.unregister
unregister_all = GLOBAL_HOOK_DISPATCHER.unregister_all


def _dispatch_schema_cascade(schema: SchemaMetadata, name: str, context: HookContext, *args: Any) -> None:
    dispatchers: tuple[HookDispatcher, ...] = (GLOBAL_HOOK_DISPATCHER, schema.hooks)
    local = schema.get_local_hook_dispatcher()
    if local is not None:
        dispatchers = (*dispatchers, local)
    _dispatch_to_all(name, dispatchers, context, *args)


def dispatch_before_process_path(
    schema: SchemaMetadata, context: HookContext, path: str, methods: dict[str, Any]
) -> None:
    _dispatch_schema_cascade(schema, "before_process_path", context, path, methods)


def dispatch_before_init_operation(schema: SchemaMetadata, context: HookContext, operation: APIOperation) -> None:
    _dispatch_schema_cascade(schema, "before_init_operation", context, operation)


def dispatch_before_load_schema(
    *dispatchers: HookDispatcher, context: HookContext, raw_schema: JsonSchemaObject
) -> None:
    _dispatch_to_all("before_load_schema", dispatchers, context, raw_schema)


def dispatch_after_load_schema(*dispatchers: HookDispatcher, context: HookContext, schema: BaseSchema) -> None:
    _dispatch_to_all("after_load_schema", dispatchers, context, schema)


def dispatch_before_add_examples(*dispatchers: HookDispatcher, context: HookContext, examples: list[Case]) -> None:
    _dispatch_to_all("before_add_examples", dispatchers, context, examples)


def dispatch_before_call(
    *dispatchers: HookDispatcher, context: HookContext, case: Case, kwargs: dict[str, Any]
) -> None:
    name = "before_call"
    for dispatcher in dispatchers:
        for hook in dispatcher.get_all_by_name(name):
            if _should_skip_hook(hook, context):
                continue
            try:
                # Support both `def before_call(ctx, case, kwargs)` and `def before_call(ctx, case, **kwargs)`.
                if has_var_keyword(hook):
                    hook(context, case, **kwargs)
                else:
                    hook(context, case, kwargs)
            except Exception as exc:
                raise HookExecutionError(name, exc) from exc


def dispatch_after_call(*dispatchers: HookDispatcher, context: HookContext, case: Case, response: Response) -> None:
    _dispatch_to_all("after_call", dispatchers, context, case, response)


def dispatch_after_network_error(
    *dispatchers: HookDispatcher, context: HookContext, case: Case, request: requests.PreparedRequest
) -> None:
    _dispatch_to_all("after_network_error", dispatchers, context, case, request)


def dispatch_after_validate(
    *dispatchers: HookDispatcher,
    context: HookContext,
    case: Case,
    response: Response,
    results: list[CheckResult],
) -> None:
    _dispatch_to_all("after_validate", dispatchers, context, case, response, results)


def hook(hook: str | Callable) -> Callable:
    """Register a new hook.

    Args:
        hook: Either a hook function (autodetecting its name) or a string matching one of the supported hook names.

    Example:
        Can be used as a decorator in two ways:

        1. Without arguments (auto-detect the hook name from the function name):

            ```python
            @schemathesis.hook
            def filter_query(ctx, query):
                \"\"\"Skip cases where query is None or invalid\"\"\"
                return query and "user_id" in query

            @schemathesis.hook
            def before_call(ctx, case, **kwargs):
                \"\"\"Modify headers before sending each request\"\"\"
                if case.headers is None:
                    case.headers = {}
                case.headers["X-Test-Mode"] = "true"
                return None
            ```

        2. With an explicit hook name as the first argument:

            ```python
            @schemathesis.hook("map_headers")
            def add_custom_header(ctx, headers):
                \"\"\"Inject a test header into every request\"\"\"
                if headers is None:
                    headers = {}
                headers["X-Custom"] = "value"
                return headers
            ```

    """
    return GLOBAL_HOOK_DISPATCHER.hook(hook)


hook.__dict__ = GLOBAL_HOOK_DISPATCHER.hook.__dict__
