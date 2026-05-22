from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable
from types import ModuleType

# bytes-shaped values are iterable but their items are ints, not module-like — treat them as scalars.
_NON_SEQUENCE_BYTES_TYPES = (bytes, bytearray, memoryview)


def resolve_modules(inputs: Iterable[object]) -> set[str]:
    """Normalise a sequence of discovery inputs to a set of module names.

    Inputs may be module objects, dotted-name strings, or any Iterable of either
    (lists, tuples, sets, generators, `dict.values()` / `Mapping.keys()` views,
    `itertools` results, etc.). App objects are handled upstream by the orchestrator.
    """
    collected: set[str] = set()
    _resolve_into(inputs, collected)
    return collected


def _resolve_into(value: object, out: set[str]) -> None:
    if isinstance(value, ModuleType):
        _add_module(value, out)
        return
    if isinstance(value, str):
        # Importing user code can raise anything (settings reads, env probes, side-effecting
        # top-level statements). Swallow the failure here so constants extraction stays
        # optional — the engine should never abort because of an unimportable module.
        try:
            module = importlib.import_module(value)
        except Exception:
            return
        _add_module(module, out)
        return
    if isinstance(value, Iterable) and not isinstance(value, _NON_SEQUENCE_BYTES_TYPES):
        for item in value:
            _resolve_into(item, out)
        return
    # App objects and other non-module values are ignored here; the orchestrator handles them.


def _add_module(module: ModuleType, out: set[str]) -> None:
    name = module.__name__
    out.add(name)
    path = getattr(module, "__path__", None)
    if path is None:
        return
    # `walk_packages` itself can raise when iterating a misconfigured package; treat the
    # whole walk as best-effort and stay silent rather than aborting extraction.
    try:
        for info in pkgutil.walk_packages(path, prefix=name + "."):
            out.add(info.name)
    except Exception:
        pass
