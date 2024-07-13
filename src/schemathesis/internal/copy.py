from typing import Any

from .extensions import extensible


@extensible("SCHEMATHESIS_EXTENSION_FAST_DEEP_COPY")
def fast_deepcopy(value: Any) -> Any:
    """A specialized version of `deepcopy` that copies only `dict` and `list` and does unrolling.

    It is on average 3x faster than `deepcopy` and given the amount of calls, it is an important optimization.
    """
    if isinstance(value, dict):
        return {
            k1: (
                {k2: fast_deepcopy(v2) for k2, v2 in v1.items()}
                if isinstance(v1, dict)
                else [fast_deepcopy(v2) for v2 in v1]
                if isinstance(v1, list)
                else v1
            )
            for k1, v1 in value.items()
        }
    if isinstance(value, list):
        return [
            {k2: fast_deepcopy(v2) for k2, v2 in v1.items()}
            if isinstance(v1, dict)
            else [fast_deepcopy(v2) for v2 in v1]
            if isinstance(v1, list)
            else v1
            for v1 in value
        ]
    return value
