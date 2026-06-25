from __future__ import annotations

import struct
from collections.abc import Callable
from math import inf
from typing import Any, TypeGuard

# Largest finite float32
FLOAT32_MAX = struct.unpack("<f", struct.pack("<I", 0x7F7FFFFF))[0]


def _float32_neighbor(value: float, *, going_up: bool) -> float:
    if value == 0.0:
        smallest = struct.unpack("<f", struct.pack("<I", 1))[0]
        return smallest if going_up else -smallest
    bits = struct.unpack("<I", struct.pack("<f", value))[0]
    if (going_up and value > 0) or (not going_up and value < 0):
        bits += 1
    else:
        bits -= 1
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def next_float32(value: int | float, *, going_up: bool) -> float:
    """Smallest single-precision float strictly above `value` when `going_up`, else strictly below.

    Beyond the float32 range the finite extreme is returned, or infinity when the requested side is empty.
    """
    try:
        value = float(value)
    except OverflowError:
        value = inf if value > 0 else -inf
    if value > FLOAT32_MAX:
        return inf if going_up else FLOAT32_MAX
    if value < -FLOAT32_MAX:
        return -FLOAT32_MAX if going_up else -inf
    rounded = struct.unpack("<f", struct.pack("<f", value))[0]
    if going_up and rounded > value:
        return rounded
    if not going_up and rounded < value:
        return rounded
    return _float32_neighbor(rounded, going_up=going_up)


def is_numeric_bound(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def bounds_are_unsatisfiable(minimum: int | float | None, maximum: int | float | None) -> bool:
    """True when no finite value satisfies the resolved bounds (an exclusive bound stepped past the range)."""
    return minimum == inf or maximum == -inf


def resolve_inclusive_bounds(
    schema: dict[str, Any], *, step: Callable[[int | float, bool], int | float]
) -> tuple[int | float | None, int | float | None]:
    """Resolve exclusive numeric bounds to inclusive ones, stepping each past the boundary via `step`.

    Handles both the Draft 4 / OpenAPI 3.0 boolean `exclusiveMinimum`/`exclusiveMaximum` form
    and the Draft 6+ / OpenAPI 3.1 numeric form. `step(value, going_up)` returns the nearest representable value
    strictly beyond `value`; the lower bound steps up, the upper bound steps down.
    """
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    exclusive_minimum = schema.get("exclusiveMinimum")
    exclusive_maximum = schema.get("exclusiveMaximum")
    if isinstance(exclusive_minimum, bool):
        if exclusive_minimum and is_numeric_bound(minimum):
            minimum = step(minimum, True)
    elif is_numeric_bound(exclusive_minimum):
        minimum = step(exclusive_minimum, True)
    if isinstance(exclusive_maximum, bool):
        if exclusive_maximum and is_numeric_bound(maximum):
            maximum = step(maximum, False)
    elif is_numeric_bound(exclusive_maximum):
        maximum = step(exclusive_maximum, False)
    return minimum, maximum
