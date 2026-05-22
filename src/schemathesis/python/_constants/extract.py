from __future__ import annotations

import importlib
import inspect
import os
import sys
import sysconfig
from collections.abc import Iterable

from schemathesis.python._constants.pool import ConstantEntry, Origin

_STDLIB_PATH = os.path.normpath(sysconfig.get_paths().get("stdlib", "")) if sysconfig.get_paths().get("stdlib") else ""


def _is_local_module(module_name: str, path: str) -> bool:
    """Check if a module is local (not from site-packages or stdlib).

    More lenient than Hypothesis's `is_local_module_file` so test fixtures
    living under `test/` are still extracted; stricter than `True` so stdlib
    and site-packages modules are skipped. When a real source path is available
    we trust it over the name-based stdlib heuristic, so a user package whose
    top-level name happens to collide with a stdlib name (e.g. a local `secrets`)
    is still extracted.
    """
    if not path:
        # Built-in / C-extension: rely on name-based stdlib detection.
        top_level = module_name.partition(".")[0]
        return top_level not in sys.stdlib_module_names
    normalised = os.path.normpath(path)
    if "site-packages" in normalised.split(os.sep):
        return False
    if _STDLIB_PATH and normalised.startswith(_STDLIB_PATH + os.sep):
        return False
    return True


def extract_from_module(module_name: str, *, origin: Origin) -> Iterable[ConstantEntry]:
    """Extract typed constants from a single module via Hypothesis's helper.

    Skips site-packages modules. Hypothesis's internal pre-filter
    (small ints, short strings cap at 20, empty values, bools, infinite floats)
    applies automatically inside `constants_from_module`.
    """
    try:
        from hypothesis.internal.constants_ast import constants_from_module
    except ImportError:
        return

    # Importing user code can raise anything (settings reads, env probes, side-effecting
    # top-level statements); the broad catch keeps extraction failures non-fatal.
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return

    # Built-in / C-extension modules raise TypeError from `getsourcefile`; treat them
    # as non-local — `_is_local_module` will reject by the stdlib-name check anyway.
    try:
        source_path = inspect.getsourcefile(module) or ""
    except TypeError:
        source_path = ""
    if not _is_local_module(module_name, source_path):
        return

    try:
        result = constants_from_module(module)
    except Exception:
        return

    for value in result.strings:
        yield ConstantEntry(value=value, type="string", origins=(origin,))
    for value in result.integers:
        yield ConstantEntry(value=value, type="integer", origins=(origin,))
    for value in result.floats:
        yield ConstantEntry(value=value, type="float", origins=(origin,))
    for value in result.bytes:
        yield ConstantEntry(value=value, type="bytes", origins=(origin,))
