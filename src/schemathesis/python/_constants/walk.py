from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable
from types import ModuleType

# bytes are iterable but yield ints, not modules - treat as scalars.
_NON_SEQUENCE_BYTES_TYPES = (bytes, bytearray, memoryview)


def resolve_modules(inputs: Iterable[object], *, walk: bool = True) -> set[str]:
    """Module names from module objects, dotted-name strings, or iterables of either.

    `walk` descends into packages. Only safe for names a source spells out - inferring one from an
    app imports whatever else happens to live in that package, side effects included.
    """
    collected: set[str] = set()
    _resolve_into(inputs, collected, walk)
    return collected


def _resolve_into(value: object, out: set[str], walk: bool) -> None:
    if isinstance(value, ModuleType):
        _add_module(value, out, walk)
        return
    if isinstance(value, str):
        # Importing user code can raise anything, including BaseException (a module calling
        # `sys.exit()` or `pytest.skip(allow_module_level=True)` at import). Extraction is
        # optional, so swallow everything except a genuine interrupt.
        try:
            module = importlib.import_module(value)
        except KeyboardInterrupt:  # pragma: no cover
            raise
        except BaseException:
            return
        _add_module(module, out, walk)
        return
    if isinstance(value, Iterable) and not isinstance(value, _NON_SEQUENCE_BYTES_TYPES):
        for item in value:
            _resolve_into(item, out, walk)
    # Non-module values (e.g. app objects) are resolved upstream by the orchestrator.


def _add_module(module: ModuleType, out: set[str], walk: bool) -> None:
    name = module.__name__
    out.add(name)
    path = getattr(module, "__path__", None)
    if path is None or not walk:
        return
    # `walk_packages` imports subpackages to descend; one that `sys.exit()`s raises BaseException.
    try:
        for info in pkgutil.walk_packages(path, prefix=f"{name}."):
            out.add(info.name)
    except KeyboardInterrupt:  # pragma: no cover
        raise
    except BaseException:
        pass
