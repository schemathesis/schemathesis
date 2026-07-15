from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
import sys
from collections.abc import Iterable
from types import ModuleType
from typing import TYPE_CHECKING

from schemathesis.python._constants.adapters import FrameworkAdapter, default_adapters, select_adapter
from schemathesis.python._constants.extract import _THIRD_PARTY_ROOTS, extract_from_module, local_imports_of
from schemathesis.python._constants.filter import is_kept
from schemathesis.python._constants.pool import DEFAULT_CAP_PER_TYPE, ConstantsPool, Origin
from schemathesis.python._constants.registry import Source, SourceRegistry, default_registry
from schemathesis.python._constants.walk import _NON_SEQUENCE_BYTES_TYPES, resolve_modules

if TYPE_CHECKING:
    from schemathesis.schemas import BaseSchema

_extraction_cache: tuple[int, ConstantsPool] | None = None


def extract_registered() -> ConstantsPool:
    # Memoise per registry version: extraction imports the SUT and walks its modules, so repeated
    # `as_strategy()`/state-machine builds against an unchanged registry must not redo that work.
    global _extraction_cache
    registry = default_registry()
    version = registry.version
    if _extraction_cache is not None and _extraction_cache[0] == version:
        return _extraction_cache[1]
    if not registry.get_all():
        pool = ConstantsPool()
    else:
        pool = extract_all(registry=registry, adapters=default_adapters())
    _extraction_cache = (version, pool)
    return pool


def build_constants_pool(schema: BaseSchema) -> ConstantsPool:
    """Build the run-level constants pool for a loaded schema.

    Combines any `@schemathesis.python.constants` sources with the application loaded via
    `from_asgi`/`from_wsgi`. Returns an empty pool when analysis is disabled in config.
    """
    if not schema.config.analysis.constants.enabled:
        return ConstantsPool()
    app = schema.app
    if app is None:
        # No app to introspect: registry-only, memoised by `extract_registered`.
        return extract_registered()
    registry = default_registry()
    version = registry.version
    cached = schema._constants_pool_cache
    if cached is not None and cached[0] is app and cached[1] == version:
        return cached[2]
    pool = extract_all(registry=registry, adapters=default_adapters(), extra_sources=[_application_source(app)])
    schema._constants_pool_cache = app, version, pool
    return pool


def make_constants_value_source(schema: BaseSchema) -> ConstantsPool | None:
    pool = build_constants_pool(schema)
    return None if pool.is_empty() else pool


def _application_source(app: object) -> Source:
    def application() -> object:
        return app

    return application


def extract_all(
    *,
    registry: SourceRegistry,
    adapters: Iterable[FrameworkAdapter],
    cap_per_type: int = DEFAULT_CAP_PER_TYPE,
    extra_sources: Iterable[Source] = (),
) -> ConstantsPool:
    """Run every registered source, walk to modules, extract + filter, build the pool."""
    pool = ConstantsPool(cap_per_type=cap_per_type)
    _extract_all(registry=registry, adapters=list(adapters), pool=pool, extra_sources=extra_sources)
    return pool


def _extract_all(
    *,
    registry: SourceRegistry,
    adapters: list[FrameworkAdapter],
    pool: ConstantsPool,
    extra_sources: Iterable[Source] = (),
) -> None:
    for source in [*registry.get_all(), *extra_sources]:
        # A user source may be a `functools.partial` or callable instance with no `__name__`.
        name = getattr(source, "__name__", repr(source))
        # A source callable, or resolving its result, can raise anything - including
        # `SystemExit` from importing user code. Swallow every failure so extraction stays
        # optional and never aborts the engine; only a genuine interrupt propagates.
        try:
            raw = source()
        except KeyboardInterrupt:
            raise
        except BaseException:
            continue

        try:
            modules, adapter_name = _resolve_with_adapters(raw, adapters=adapters)
        except KeyboardInterrupt:
            raise
        except BaseException:
            continue

        for module_name in sorted(modules):
            origin = Origin(source=name, module=module_name, adapter=adapter_name)
            for entry in extract_from_module(module_name, origin=origin):
                if is_kept(entry.value, entry.type):
                    pool.add(entry)


def _resolve_with_adapters(raw: object, *, adapters: list[FrameworkAdapter]) -> tuple[set[str], str | None]:
    """Resolve a discovery input to a (module_set, adapter_name) pair.

    Strings/modules resolve directly; iterables recurse per item; other objects try the
    adapters, falling back to the object's top-level package.
    """
    if isinstance(raw, (str, ModuleType)):
        return resolve_modules([raw]), None
    if raw is None:
        return set(), None
    if isinstance(raw, Iterable) and not isinstance(raw, _NON_SEQUENCE_BYTES_TYPES):
        # Recurse so every item resolves like a top-level source; report the first adapter seen.
        aggregated_modules: set[str] = set()
        aggregated_adapter: str | None = None
        for item in raw:
            item_modules, item_adapter = _resolve_with_adapters(item, adapters=adapters)
            aggregated_modules.update(item_modules)
            if aggregated_adapter is None:
                aggregated_adapter = item_adapter
        return aggregated_modules, aggregated_adapter

    adapter = select_adapter(raw, adapters=adapters)
    if adapter is not None:
        try:
            handlers = list(adapter.handlers(raw))
            declared = set(adapter.modules(raw))
        except Exception:
            return set(), None
        # Both are inferred from the app, so neither is known to be user code: a library view or a
        # library-built app would otherwise harvest its framework's internals.
        modules = {name for name in _modules_for_handlers(handlers) | declared if _is_likely_user_package(name)}
        return resolve_modules(_expand_with_local_imports(modules), walk=False), adapter.name

    # Unknown framework: fall back to the module defining the app, but only when its package looks
    # like user code -- else a niche framework's instance would harvest its own internals.
    module_name = getattr(raw, "__module__", None)
    if isinstance(module_name, str) and module_name:
        top_level = module_name.partition(".")[0]
        if _is_likely_user_package(top_level):
            return resolve_modules(_expand_with_local_imports({module_name}), walk=False), None
    return set(), None


def _is_likely_user_package(name: str) -> bool:
    """True if `name` refers to a top-level package that looks like user code."""
    if name in sys.stdlib_module_names:
        return False
    if name in ("builtins", "__main__"):
        return False
    try:
        spec = importlib.util.find_spec(name)
    except Exception:
        return False
    if spec is None:
        return False
    origin = spec.origin or ""
    if origin:
        normalized = os.path.realpath(origin)
        for root in _THIRD_PARTY_ROOTS:
            if normalized.startswith(root + os.sep):
                return False
    # No origin (namespace package) - accept; the walker will discover its submodules.
    return True


def _modules_for_handlers(handlers: Iterable[object]) -> set[str]:
    modules: set[str] = set()
    for handler in handlers:
        module = inspect.getmodule(handler)
        if module is not None:
            modules.add(module.__name__)
    return modules


def _expand_with_local_imports(module_names: set[str]) -> set[str]:
    """Handler modules plus the local modules they directly import (one level deep)."""
    expanded = set(module_names)
    for name in module_names:
        expanded |= local_imports_of(name)
    return expanded
