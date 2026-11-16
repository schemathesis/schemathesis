from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from collections.abc import Iterable
from types import ModuleType
from typing import TYPE_CHECKING

from schemathesis.python._constants.extract import _THIRD_PARTY_ROOTS, extract_from_module, local_imports_of
from schemathesis.python._constants.filter import is_kept
from schemathesis.python._constants.pool import ConstantsPool, Origin
from schemathesis.python._constants.registry import Source, SourceRegistry, default_registry
from schemathesis.python._constants.walk import _NON_SEQUENCE_BYTES_TYPES, resolve_modules
from schemathesis.python.application import application_code

if TYPE_CHECKING:
    from schemathesis.schemas import BaseSchema

_extraction_cache: tuple[int, ConstantsPool] | None = None


def extract_registered() -> ConstantsPool:
    # Memoised per registry version: extraction imports the app under test and walks its modules.
    global _extraction_cache
    registry = default_registry()
    version = registry.version
    if _extraction_cache is not None and _extraction_cache[0] == version:
        return _extraction_cache[1]
    if not registry.get_all():
        pool = ConstantsPool()
    else:
        pool = extract_all(registry=registry)
    _extraction_cache = (version, pool)
    return pool


def build_constants_pool(schema: BaseSchema) -> ConstantsPool:
    """Build the run-level constants pool from `@schemathesis.python.constants` sources and the loaded ASGI/WSGI app."""
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
    pool = extract_all(registry=registry, extra_sources=[_application_source(app)])
    schema._constants_pool_cache = app, version, pool
    return pool


def make_constants_value_source(schema: BaseSchema) -> ConstantsPool | None:
    pool = build_constants_pool(schema)
    return None if pool.is_empty() else pool


def _application_source(app: object) -> Source:
    def application() -> object:
        return app

    return application


def extract_all(*, registry: SourceRegistry, extra_sources: Iterable[Source] = ()) -> ConstantsPool:
    """Run every registered source and pool the constants of the modules they resolve to."""
    pool = ConstantsPool()
    _extract_all(registry=registry, pool=pool, extra_sources=extra_sources)
    return pool


def _extract_all(*, registry: SourceRegistry, pool: ConstantsPool, extra_sources: Iterable[Source] = ()) -> None:
    # Failures of registered sources are recorded; the best-effort app source stays quiet without reusable literals.
    sources = [(source, True) for source in registry.get_all()] + [(source, False) for source in extra_sources]
    for source, registered in sources:
        # A user source may be a `functools.partial` or callable instance with no `__name__`.
        name = getattr(source, "__name__", repr(source))
        # Sources can raise anything, even `SystemExit` from imports; extraction is optional, so only interrupts pass.
        try:
            raw = source()
        except KeyboardInterrupt:
            raise
        except BaseException as exc:
            if registered:
                pool.record_failure(name, _failure_reason(exc))
            continue

        try:
            modules, adapter_name = _resolve_with_adapters(raw)
        except KeyboardInterrupt:
            raise
        except BaseException as exc:
            if registered:
                pool.record_failure(name, _failure_reason(exc))
            continue

        if not modules:
            # A registered source with no modules to scan is misconfigured; no keepable literals is not an error.
            if registered:
                pool.record_failure(name, "resolved to no modules to scan")
            continue

        for module_name in sorted(modules):
            origin = Origin(source=name, module=module_name, adapter=adapter_name)
            for entry in extract_from_module(module_name, origin=origin):
                if is_kept(entry.value, entry.type):
                    pool.add(entry)


def _failure_reason(exc: BaseException) -> str:
    # First line only: a multi-line reason would break the CLI warning block layout.
    text = str(exc).strip().splitlines()
    first = text[0].strip() if text else ""
    return f"{type(exc).__name__}: {first}" if first else type(exc).__name__


def _resolve_with_adapters(raw: object) -> tuple[set[str], str | None]:
    """Resolve a discovery input to (modules, adapter name); objects no adapter knows use their top-level package."""
    if isinstance(raw, (str, ModuleType)):
        return resolve_modules([raw]), None
    if raw is None:
        return set(), None
    if isinstance(raw, Iterable) and not isinstance(raw, _NON_SEQUENCE_BYTES_TYPES):
        # Recurse so every item resolves like a top-level source; report the first adapter seen.
        aggregated_modules: set[str] = set()
        aggregated_adapter: str | None = None
        for item in raw:
            item_modules, item_adapter = _resolve_with_adapters(item)
            aggregated_modules.update(item_modules)
            if aggregated_adapter is None:
                aggregated_adapter = item_adapter
        return aggregated_modules, aggregated_adapter

    code = application_code(raw)
    if code is not None:
        # Neither is known to be user code: a library view or library-built app would harvest its framework's internals.
        modules = {name for name in code.modules if _is_likely_user_package(name)}
        return resolve_modules(_expand_with_local_imports(modules), walk=False), code.framework

    # Unknown framework: use the module defining the app if it looks like user code, not a framework's internals.
    module_name = getattr(raw, "__module__", None)
    if isinstance(module_name, str) and module_name:
        top_level = module_name.partition(".")[0]
        if _is_likely_user_package(top_level):
            return resolve_modules(_expand_with_local_imports({module_name}), walk=False), None
    return set(), None


def _is_likely_user_package(name: str) -> bool:
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
    # No origin (namespace package): accept; the walker discovers its submodules.
    return True


def _expand_with_local_imports(module_names: set[str]) -> set[str]:
    """Handler modules plus the local modules they directly import (one level deep)."""
    expanded = set(module_names)
    for name in module_names:
        expanded |= local_imports_of(name)
    return expanded
