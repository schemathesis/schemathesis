from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum, unique
from functools import lru_cache, partial
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypeVar, cast

import jsonschema_rs

from schemathesis.config import ConfigError, SchemathesisConfig
from schemathesis.config._parameters import validate_binding_key
from schemathesis.core import SpecificationFeature
from schemathesis.core.errors import HookExecutionError, format_exception
from schemathesis.core.jsonschema.types import JsonSchemaObject
from schemathesis.core.marks import Mark
from schemathesis.core.transport import Response
from schemathesis.filters import FilterSet, attach_filter_chain
from schemathesis.generation.suggestions import HOOKS_SOURCE

if TYPE_CHECKING:
    import requests
    from hypothesis import strategies as st

    from schemathesis.checks import CheckResult
    from schemathesis.config import ProjectConfig
    from schemathesis.core.failures import Failure
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
    _schema: SchemaMetadata | None
    # The hook being called, to tell which extension a suggestion comes from
    _hook: Callable | None

    def __init__(self, *, operation: APIOperation | None = None, schema: SchemaMetadata | None = None) -> None:
        self.operation = operation
        self._schema = schema if schema is not None or operation is None else operation.schema
        self._hook = None

    def suggest(self, *schemas: JsonSchemaObject, parameter: str, operation: str | None = None) -> None:
        # Each schema is a JSON Schema for the values to send: `{"const": 57}`, `{"enum": ["open", "held"]}`,
        # `{"type": "string", "pattern": "^VC-[0-9]{4}$"}`. Every member of a fixed set is a suggestion of its own;
        # any other schema is one suggestion, and each use draws a value it admits.
        # `parameter` uses the keys of `[parameters]` in the config. `operation` is a label, by default the operation
        # this hook runs for, or every operation.
        if self._schema is None:
            raise RuntimeError("`suggest` needs a loaded schema; `before_load_schema` runs before there is one")
        specification = self._schema.specification
        if not specification.supports_feature(SpecificationFeature.SUGGESTIONS):
            raise ValueError(f"`suggest` does not work with {specification.name} yet")
        try:
            validate_binding_key(parameter)
        except ConfigError as error:
            raise ValueError(str(error)) from None
        for schema in schemas:
            try:
                jsonschema_rs.meta.validate(schema)
            except jsonschema_rs.ValidationError as error:
                raise ValueError(f"Invalid schema: {error.message}") from None
        if operation is None and self.operation is not None:
            operation = self.operation.label
        source = _extension_name(self._hook) if self._hook is not None else None
        self._schema.suggestions.add(schemas, source=source or HOOKS_SOURCE, operation=operation, parameter=parameter)


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
                # Filters chained on the named form apply only to the hook this decorator registers
                func.filter_set = decorator_filter_set  # type: ignore[attr-defined]
                return dispatcher.register_hook_with_name(func, hook_name)

            decorator_filter_set = init_filter_set(decorator)
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

    def applicable(self, name: str, context: HookContext) -> Iterator[Callable]:
        """Hooks registered for a name whose filters accept the operation in `context`."""
        for hook in self.get_all_by_name(name):
            if not _should_skip_hook(hook, context):
                yield hook

    def defines(self, name: str) -> bool:
        """Return True if any hooks are registered under the given name."""
        return bool(self._hooks.get(name))

    def get_all(self) -> dict[str, list[Callable]]:
        return self._hooks

    def apply_to_container(
        self,
        strategy: st.SearchStrategy,
        container: ContainerName | Literal["case"],
        context: HookContext,
        *,
        filter_wrapper: Callable[[Callable], Callable] | None = None,
        map_wrapper: Callable[[Callable], Callable] | None = None,
        flatmap_wrapper: Callable[[Callable], Callable] | None = None,
    ) -> st.SearchStrategy:
        for hook in self.applicable(f"before_generate_{container}", context):
            strategy = hook(context, strategy)
        for hook in self.applicable(f"filter_{container}", context):
            hook = partial(hook, context)
            if filter_wrapper is not None:
                hook = filter_wrapper(hook)
            strategy = strategy.filter(hook)
        for hook in self.applicable(f"map_{container}", context):
            hook = partial(hook, context)
            if map_wrapper is not None:
                hook = map_wrapper(hook)
            strategy = strategy.map(hook)
        for hook in self.applicable(f"flatmap_{container}", context):
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
        for hook in dispatcher.applicable(name, context):
            context._hook = hook
            try:
                hook(context, *args)
            except (Exception, SystemExit) as exc:
                raise HookExecutionError(name, exc, extension=_extension_name(hook)) from exc


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
    if name in ("before_process_path", "before_load_schema", "after_load_schema", "setup", "teardown"):
        raise ValueError(f"Filters are not applicable to this hook: `{name}`")


# Hook spec definitions live in `hook_specs`; importing the module triggers registration.
from schemathesis import hook_specs  # noqa: E402, F401

GLOBAL_HOOK_DISPATCHER = HookDispatcher(scope=HookScope.GLOBAL)
get_all_by_name = GLOBAL_HOOK_DISPATCHER.get_all_by_name
defines = GLOBAL_HOOK_DISPATCHER.defines
unregister = GLOBAL_HOOK_DISPATCHER.unregister
unregister_all = GLOBAL_HOOK_DISPATCHER.unregister_all


def schema_hook_dispatchers(schema: SchemaMetadata) -> tuple[HookDispatcher, ...]:
    """Global, schema-level, and test-scoped dispatchers, in the order their hooks run."""
    local = schema.get_local_hook_dispatcher()
    if local is None:
        return (GLOBAL_HOOK_DISPATCHER, schema.hooks)
    return (GLOBAL_HOOK_DISPATCHER, schema.hooks, local)


def _dispatch_schema_cascade(schema: SchemaMetadata, name: str, context: HookContext, *args: Any) -> None:
    _dispatch_to_all(name, schema_hook_dispatchers(schema), context, *args)


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


SchemaT = TypeVar("SchemaT", bound="BaseSchema")


def load_with_hooks(
    raw_schema: JsonSchemaObject, config: SchemathesisConfig | None, build: Callable[[ProjectConfig], SchemaT]
) -> SchemaT:
    """Build a schema from `raw_schema` under its project config, between the global load hooks."""
    context = HookContext()
    dispatch_before_load_schema(GLOBAL_HOOK_DISPATCHER, context=context, raw_schema=raw_schema)
    if config is None:
        config = SchemathesisConfig.discover()
    project_config = config.projects.get(raw_schema)
    instance = build(project_config)
    instance.filter_set = project_config.operations.filter_set_with(include=instance.filter_set)
    context._schema = instance
    dispatch_after_load_schema(GLOBAL_HOOK_DISPATCHER, context=context, schema=instance)
    return instance


def dispatch_setup(schema: SchemaMetadata) -> None:
    _dispatch_schema_cascade(schema, "setup", HookContext(schema=schema), schema)


def defines_teardown(schema: SchemaMetadata) -> bool:
    return any(dispatcher.defines("teardown") for dispatcher in schema_hook_dispatchers(schema))


def dispatch_teardown(schema: SchemaMetadata) -> list[HookExecutionError]:
    """Run every `teardown` hook, even after one fails, and return the errors they raised."""
    context = HookContext(schema=schema)
    errors = []
    # A later hook may build on what an earlier one set up, so it is torn down first.
    for dispatcher in reversed(schema_hook_dispatchers(schema)):
        for hook in reversed(dispatcher.get_all_by_name("teardown")):
            context._hook = hook
            try:
                hook(context, schema)
            except (Exception, SystemExit) as exc:
                errors.append(HookExecutionError("teardown", exc, extension=_extension_name(hook)))
    return errors


def dispatch_before_add_examples(*dispatchers: HookDispatcher, context: HookContext, examples: list[Case]) -> None:
    _dispatch_to_all("before_add_examples", dispatchers, context, examples)


def dispatch_before_call(
    *dispatchers: HookDispatcher, context: HookContext, case: Case, kwargs: dict[str, Any]
) -> None:
    name = "before_call"
    for dispatcher in dispatchers:
        for hook in dispatcher.applicable(name, context):
            context._hook = hook
            try:
                # Support both `def before_call(ctx, case, kwargs)` and `def before_call(ctx, case, **kwargs)`.
                if has_var_keyword(hook):
                    hook(context, case, **kwargs)
                else:
                    hook(context, case, kwargs)
            except (Exception, SystemExit) as exc:
                raise HookExecutionError(name, exc, extension=_extension_name(hook)) from exc


def dispatch_after_call(*dispatchers: HookDispatcher, context: HookContext, case: Case, response: Response) -> None:
    _dispatch_to_all("after_call", dispatchers, context, case, response)


def dispatch_after_network_error(
    *dispatchers: HookDispatcher, context: HookContext, case: Case, request: requests.PreparedRequest
) -> None:
    _dispatch_to_all("after_network_error", dispatchers, context, case, request)


def should_keep_failure(failure: Failure, case: Case, response: Response) -> bool:
    """Whether every `filter_failure` hook accepts this failure. One rejection is final."""
    name = "filter_failure"
    dispatchers = schema_hook_dispatchers(case.operation.schema)
    if not any(dispatcher.defines(name) for dispatcher in dispatchers):
        return True
    context = HookContext(operation=case.operation)
    for dispatcher in dispatchers:
        for hook in dispatcher.applicable(name, context):
            context._hook = hook
            try:
                if not hook(context, failure, case, response):
                    return False
            except (Exception, SystemExit) as exc:
                raise HookExecutionError(name, exc, extension=_extension_name(hook)) from exc
    return True


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


# Registered extension classes by name, in registration order.
EXTENSIONS: dict[str, type] = {}


def extension(name: str) -> Callable[[type], type]:
    """Register a class whose methods are hooks; each `st run` / `st fuzz` gets one instance.

    The instance is created with the `[extensions.<name>]` table from the config as a dict.
    Public methods must be hook names or `handle_event`, which receives CLI events.

    Example:
        ```python
        @schemathesis.extension("taint")
        class Taint:
            def __init__(self, settings):
                self.depth = settings.get("depth", 3)

            def after_call(self, context, case, response):
                ...
        ```

    """
    if not isinstance(name, str):
        raise TypeError('Extension name is missing; use `@schemathesis.extension("name")`')

    def register(cls: type) -> type:
        if not isinstance(cls, type):
            raise TypeError(f"Extension `{name}` must be a class, got `{type(cls).__name__}`")
        registered = EXTENSIONS.get(name)
        if registered is cls:
            return cls
        if registered is not None:
            raise ValueError(f"Extension `{name}` is already registered by `{registered.__qualname__}`")
        other = next((other for other, existing in EXTENSIONS.items() if existing is cls), None)
        if other is not None:
            raise ValueError(f"`{cls.__qualname__}` is already registered as extension `{other}`")
        for attribute in _hook_methods(cls):
            _validate_extension_method(cls, attribute)
        _validate_arguments(cls, 1, f"`{cls.__qualname__}` must take the settings table: `__init__(self, settings)`")
        handle_event = getattr(cls, "handle_event", None)
        if handle_event is not None:
            _validate_arguments(handle_event, 3, f"`{cls.__qualname__}.handle_event` must take `(self, ctx, event)`")
        EXTENSIONS[name] = cls
        if _extension_listener is not None:
            _extension_listener(name, cls)
        return cls

    return register


_extension_listener: Callable[[str, type], None] | None = None


def set_extension_listener(listener: Callable[[str, type], None] | None) -> Callable[[str, type], None] | None:
    """Call `listener` with every extension registered from now on; returns the listener it replaces."""
    global _extension_listener
    previous, _extension_listener = _extension_listener, listener
    return previous


def _signature(target: Callable) -> inspect.Signature | None:
    try:
        return inspect.signature(target)
    except (TypeError, ValueError):
        # Native callables may not expose a signature
        return None


def _validate_arguments(target: Callable, count: int, message: str) -> None:
    signature = _signature(target)
    if signature is not None:
        try:
            signature.bind(*[None] * count)
        except TypeError:
            raise TypeError(message) from None


def _hook_methods(cls: type) -> Iterator[str]:
    for attribute in dir(cls):
        if not attribute.startswith("_") and attribute != "handle_event" and callable(getattr(cls, attribute)):
            yield attribute


def _validate_extension_method(cls: type, attribute: str) -> None:
    spec = HookDispatcher._specs.get(attribute)
    qualname = f"{cls.__qualname__}.{attribute}"
    if spec is None or HookScope.GLOBAL not in spec.scopes:
        raise TypeError(f"`{qualname}` is not a hook; extension methods must be hooks or `handle_event`")
    signature = _signature(getattr(cls, attribute))
    if signature is None:
        return
    defined = len(signature.parameters) - 1
    expected = len(spec.signature.parameters)
    if defined != expected:
        raise TypeError(f"`{qualname}` takes {defined} arguments, but the `{attribute}` hook passes {expected}")


def _extension_name(hook: Callable) -> str | None:
    owner = type(getattr(hook, "__self__", None))
    return next((name for name, cls in EXTENSIONS.items() if cls is owner), None)


def check_extension_tables(settings: dict[str, dict[str, object]]) -> None:
    for name in settings:
        if name not in EXTENSIONS:
            if EXTENSIONS:
                hint = "loaded: " + ", ".join(f"`{loaded}`" for loaded in EXTENSIONS)
            else:
                hint = "add the extension's module to `hooks`"
            raise ConfigError(f"`[extensions.{name}]` matches no loaded extension; {hint}")


def create_extension(name: str, cls: type, settings: dict[str, object]) -> object:
    try:
        return cls(settings)
    except ValueError as exc:
        raise ConfigError(f"Extension `{name}` rejected its settings: {exc}") from exc
    except Exception as exc:
        trace = format_exception(exc, with_traceback=True, skip_frames=1)
        raise ConfigError(f"Extension `{name}` failed to start\n\n{trace}") from exc


def bind_extension(instance: object) -> None:
    """Register the hook methods of an extension instance globally."""
    for attribute in _hook_methods(type(instance)):
        # Validated at registration; native methods may lack the signature that plain hook registration checks
        GLOBAL_HOOK_DISPATCHER._hooks[attribute].append(getattr(instance, attribute))


def bind_extensions(settings: dict[str, dict[str, object]]) -> dict[str, object]:
    """Create every registered extension for one run and register its hook methods globally."""
    check_extension_tables(settings)
    instances = {name: create_extension(name, cls, settings.get(name, {})) for name, cls in EXTENSIONS.items()}
    for instance in instances.values():
        bind_extension(instance)
    return instances


def unbind_extensions(instances: dict[str, object]) -> None:
    """Remove the hook methods of these extension instances."""
    owners = list(instances.values())
    for hooks in GLOBAL_HOOK_DISPATCHER._hooks.values():
        hooks[:] = [hook for hook in hooks if not any(getattr(hook, "__self__", None) is owner for owner in owners)]
