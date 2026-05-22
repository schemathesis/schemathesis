from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from types import ModuleType
from typing import Literal

from schemathesis.python._constants.adapters import FrameworkAdapter, select_adapter
from schemathesis.python._constants.extract import extract_from_module
from schemathesis.python._constants.filter import is_kept
from schemathesis.python._constants.pool import ConstantsPool, Origin
from schemathesis.python._constants.registry import SourceRegistry
from schemathesis.python._constants.walk import resolve_modules

DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_CAP_PER_TYPE = 5000

# `bytes`-shaped values iterate as ints — treat as scalars, not sequences of inputs.
_NON_SEQUENCE_BYTES_TYPES = (bytes, bytearray, memoryview)


@dataclass(slots=True, frozen=True)
class ExtractionError:
    source: str
    reason: Literal["source_error", "adapter_error", "import_error", "extraction_error"]
    detail: str


@dataclass(slots=True)
class ExtractionResult:
    pool: ConstantsPool
    errors: list[ExtractionError] = field(default_factory=list)
    timed_out: bool = False
    elapsed_seconds: float = 0.0
    per_source: dict[str, int] = field(default_factory=dict)
    per_adapter: dict[str | None, int] = field(default_factory=dict)
    module_count: int = 0


def extract_all(
    *,
    registry: SourceRegistry,
    adapters: Iterable[FrameworkAdapter],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    cap_per_type: int = DEFAULT_CAP_PER_TYPE,
) -> ExtractionResult:
    """Run every registered source, walk to modules, extract + filter, build the pool."""
    pool = ConstantsPool(cap_per_type=cap_per_type)
    result = ExtractionResult(pool=pool)
    adapters_list = list(adapters)
    visited_modules: set[str] = set()

    start = time.monotonic()
    deadline = start + timeout_seconds

    for registration in registry.entries():
        if time.monotonic() >= deadline:
            result.timed_out = True
            break

        try:
            raw = registration.callable()
        except Exception as exc:
            result.errors.append(ExtractionError(source=registration.name, reason="source_error", detail=str(exc)))
            continue

        # After the callable returns, check deadline again — the callable may have blocked
        if time.monotonic() >= deadline:
            result.timed_out = True
            break

        # Generator-shaped sources can raise after the callable returns — partway through
        # iteration, when their item access triggers user code. Treat those failures as
        # `source_error` so extraction stays optional and the engine never aborts.
        try:
            modules, adapter_name = _resolve_with_adapters(
                raw, adapters=adapters_list, result=result, source=registration.name
            )
        except Exception as exc:
            result.errors.append(ExtractionError(source=registration.name, reason="source_error", detail=str(exc)))
            continue

        source_contributed = 0
        # Sort to make eviction order under cap_per_type independent of PYTHONHASHSEED.
        for module_name in sorted(modules):
            if module_name in visited_modules:
                continue
            visited_modules.add(module_name)
            if time.monotonic() >= deadline:
                result.timed_out = True
                break

            origin = Origin(source=registration.name, module=module_name, adapter=adapter_name)
            try:
                for entry in extract_from_module(module_name, origin=origin):
                    if is_kept(entry.value, entry.type):
                        pool.add(entry)
                        source_contributed += 1
            except Exception as exc:
                result.errors.append(
                    ExtractionError(source=registration.name, reason="extraction_error", detail=str(exc))
                )

        result.per_source[registration.name] = result.per_source.get(registration.name, 0) + source_contributed
        result.per_adapter[adapter_name] = result.per_adapter.get(adapter_name, 0) + source_contributed

    result.elapsed_seconds = time.monotonic() - start
    result.module_count = len(visited_modules)
    return result


def _resolve_with_adapters(
    raw: object,
    *,
    adapters: list[FrameworkAdapter],
    result: ExtractionResult,
    source: str,
) -> tuple[set[str], str | None]:
    """Resolve a discovery input to a (module_set, adapter_name) pair.

    Routing:
    * Strings and module objects → single input for the walker.
    * Any other `Iterable` (lists, tuples, sets, generators, `dict.values()` / `Mapping.keys()`
      views, `itertools` results, custom Iterable subclasses) → sequence of inputs.
    * Other objects → try adapters; on no match, fall back to the user's top-level package.
    """
    if isinstance(raw, (str, ModuleType)):
        return resolve_modules([raw]), None
    if raw is None:
        return set(), None
    if isinstance(raw, Iterable) and not isinstance(raw, _NON_SEQUENCE_BYTES_TYPES):
        # Recurse so each item gets the same adapter/fallback resolution as a top-level
        # source — a list like `[fastapi_app, "my_app.routes"]` must invoke the adapter
        # for the app and import the module name. Aggregate modules and report the first
        # non-None adapter (best-effort attribution; multiple adapters in one source are rare).
        aggregated_modules: set[str] = set()
        aggregated_adapter: str | None = None
        for item in raw:
            item_modules, item_adapter = _resolve_with_adapters(item, adapters=adapters, result=result, source=source)
            aggregated_modules.update(item_modules)
            if aggregated_adapter is None:
                aggregated_adapter = item_adapter
        return aggregated_modules, aggregated_adapter

    adapter = select_adapter(raw, adapters=adapters, errors=result.errors, source=source)
    if adapter is not None:
        try:
            handlers = list(adapter.handlers(raw))
        except Exception as exc:
            result.errors.append(ExtractionError(source=source, reason="adapter_error", detail=str(exc)))
            return set(), None
        return resolve_modules(_modules_for_handlers(handlers)), adapter.name

    # Unknown framework: walk the user-visible top-level package of the object's module,
    # but only when it looks like user code. Without this guard, instances of a niche
    # framework would route to the framework's own package (e.g. `Sanic` → "sanic"),
    # harvesting framework internals instead of the user's constants.
    module_name = getattr(raw, "__module__", None)
    if isinstance(module_name, str) and module_name:
        top_level = module_name.partition(".")[0]
        if _is_likely_user_package(top_level):
            return resolve_modules([top_level]), None
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
    if "site-packages" in origin:
        return False
    # No origin (namespace package) — accept; the walker will discover its submodules.
    return True


def _modules_for_handlers(handlers: Iterable[object]) -> set[str]:
    modules: set[str] = set()
    for handler in handlers:
        module = inspect.getmodule(handler)
        if module is not None:
            modules.add(module.__name__)
    return modules
