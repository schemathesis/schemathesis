from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import os
import site
import sys
import sysconfig
from collections.abc import Iterable

from schemathesis.python._constants.pool import ConstantEntry, Origin


def _third_party_roots() -> tuple[str, ...]:
    """Install roots (site-packages, Debian dist-packages, user site) whose modules aren't local."""
    candidates: list[str] = []
    paths = sysconfig.get_paths()
    # purelib + platlib differ only on Debian/Ubuntu (dist-packages); include both to cover it.
    for key in ("purelib", "platlib"):
        path = paths.get(key)
        if path:
            candidates.append(path)
    candidates.extend(site.getsitepackages())
    user_site = site.getusersitepackages()
    if user_site:
        candidates.append(user_site)
    unique: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        normalized = os.path.realpath(path)
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return tuple(unique)


_THIRD_PARTY_ROOTS = _third_party_roots()
_stdlib = sysconfig.get_paths().get("stdlib")
_STDLIB_PATH = os.path.realpath(_stdlib) if _stdlib else ""


def _is_local_module(module_name: str, path: str) -> bool:
    """Whether a module is user/local code, not stdlib or an installed package.

    A real source `path` wins over the name heuristic, so a local `secrets.py` is
    still extracted despite colliding with a stdlib name.

    Examples:
        ("myapp.views", "/proj/myapp/views.py")                 -> True
        ("requests", "/venv/site-packages/requests/__init__.py") -> False
        ("json", "/usr/lib/python3.12/json/__init__.py")         -> False
        ("itertools", "")                                        -> False  # builtin, name-based

    """
    if not path:
        # Built-in / C-extension: rely on name-based stdlib detection.
        top_level = module_name.partition(".")[0]
        return top_level not in sys.stdlib_module_names
    normalized = os.path.realpath(path)
    for root in _THIRD_PARTY_ROOTS:
        if normalized.startswith(root + os.sep):
            return False
    if _STDLIB_PATH and normalized.startswith(_STDLIB_PATH + os.sep):
        return False
    return True


def local_imports_of(module_name: str) -> set[str]:
    """Local modules that `module_name` imports directly, resolved from its source.

    `from x import y` binds `y`, not `x`, so an import target isn't recoverable from the
    imported module's namespace - parse the source to see what it depends on. Handler
    modules are usually thin, and the constants worth reusing live in the enum/config/model
    modules they import.
    """
    try:
        module = importlib.import_module(module_name)
    except KeyboardInterrupt:  # pragma: no cover
        raise
    except BaseException:
        return set()
    try:
        source = inspect.getsource(module)
    except (OSError, TypeError):
        return set()
    tree = ast.parse(source)

    package = module.__package__ or ""
    candidates: set[str] = set()
    for node in _module_level_imports(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                candidates.add(alias.name)
        else:
            base = _absolute_import_base(node, package)
            if base:
                candidates.add(base)
            # `from pkg import name` may pull in either a submodule or an attribute; keep both
            # candidates and let the local-spec check drop whichever isn't a real module.
            for alias in node.names:
                candidates.add(f"{base}.{alias.name}" if base else alias.name)
    return {name for name in candidates if _is_local_import(name)}


def _module_level_imports(tree: ast.Module) -> Iterable[ast.Import | ast.ImportFrom]:
    """Module-scope imports, descending only `if`/`try` guards (TYPE_CHECKING, optional deps).

    Skips function and class bodies; `ast.walk` visits every node and dominates extraction
    time on large modules.
    """
    stack: list[ast.stmt] = list(reversed(tree.body))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        elif isinstance(node, ast.If):
            stack.extend(reversed(node.body))
            stack.extend(reversed(node.orelse))
        elif isinstance(node, ast.Try):
            stack.extend(reversed(node.body))
            stack.extend(reversed(node.orelse))
            stack.extend(reversed(node.finalbody))
            for handler in node.handlers:
                stack.extend(reversed(handler.body))


def _absolute_import_base(node: ast.ImportFrom, package: str) -> str:
    """Absolute module an `ImportFrom` reads from, resolving relative imports against `package`."""
    if node.level == 0:
        return node.module or ""
    parts = package.split(".") if package else []
    prefix = ".".join(parts[: len(parts) - (node.level - 1)])
    if node.module:
        return f"{prefix}.{node.module}" if prefix else node.module
    return prefix


def _is_local_import(name: str) -> bool:
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, AttributeError, ValueError):
        return False
    if spec is None:
        return False
    return _is_local_module(name, spec.origin or "")


def extract_from_module(module_name: str, *, origin: Origin) -> Iterable[ConstantEntry]:
    """Extract typed constants from a single module via Hypothesis's helper.

    Skips site-packages modules. Hypothesis's internal pre-filter
    (small ints, short strings cap at 20, empty values, bools, infinite floats)
    applies automatically inside `constants_from_module`.
    """
    try:
        from hypothesis.internal.constants_ast import constants_from_module
    except ImportError:  # pragma: no cover
        return

    # Importing user code can raise anything (settings reads, env probes, side-effecting
    # top-level statements) including BaseException like `sys.exit()`; the broad catch keeps
    # extraction failures non-fatal. Only a genuine interrupt is allowed through.
    try:
        module = importlib.import_module(module_name)
    except KeyboardInterrupt:  # pragma: no cover
        raise
    except BaseException:
        return

    # Built-in / C-extension modules raise TypeError from `getsourcefile`; treat them
    # as non-local - `_is_local_module` will reject by the stdlib-name check anyway.
    try:
        source_path = inspect.getsourcefile(module) or ""
    except TypeError:
        source_path = ""
    if not _is_local_module(module_name, source_path):
        return

    result = constants_from_module(module)

    for value in sorted(result.strings):
        yield ConstantEntry(value=value, type="string", origins=(origin,))
    for value in sorted(result.integers):
        yield ConstantEntry(value=value, type="integer", origins=(origin,))
    for value in sorted(result.floats):
        yield ConstantEntry(value=value, type="float", origins=(origin,))
    for value in sorted(result.bytes):
        yield ConstantEntry(value=value, type="bytes", origins=(origin,))
