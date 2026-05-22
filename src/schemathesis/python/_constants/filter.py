from __future__ import annotations

import math
import re

from schemathesis.python._constants.pool import ConstantType

_DOTTED_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")

_HTTP_STATUSES = frozenset({200, 201, 204, 301, 302, 400, 401, 403, 404, 422, 500, 502, 503})
_TRIVIAL_INTEGERS = frozenset({0, 1, -1})
_TRIVIAL_FLOATS = frozenset({0.0, 1.0, -1.0})


def is_kept(value: object, type_: ConstantType) -> bool:
    # Pair the Literal dispatch with `isinstance` so mypy narrows the primitive at each branch.
    if type_ == "string" and isinstance(value, str):
        return _keep_string(value)
    if type_ == "integer" and isinstance(value, int) and not isinstance(value, bool):
        return _keep_integer(value)
    if type_ == "float" and isinstance(value, float):
        return _keep_float(value)
    if type_ == "bytes" and isinstance(value, bytes):
        return _keep_bytes(value)
    return False


def _keep_string(value: str) -> bool:
    if "\n" in value:
        return False
    if _DOTTED_IDENT.match(value):
        return False
    if value.startswith("/") or value.startswith("."):
        return False
    return True


def _keep_integer(value: int) -> bool:
    if value in _TRIVIAL_INTEGERS:
        return False
    if value in _HTTP_STATUSES:
        return False
    return True


def _keep_float(value: float) -> bool:
    if not math.isfinite(value):
        return False
    if value in _TRIVIAL_FLOATS:
        return False
    return True


def _keep_bytes(value: bytes) -> bool:
    if b"\n" in value:
        return False
    if len(value) > 32:
        return False
    return True
