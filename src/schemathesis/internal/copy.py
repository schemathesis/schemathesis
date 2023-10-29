from typing import Any


def fast_deepcopy(value: Any) -> Any:
    """A specialized version of `deepcopy` that copies only `dict` and `list`.

    It is on average 3x faster than `deepcopy` and given the amount of calls, it is an important optimization.
    """
    if isinstance(value, dict):
        return {key: fast_deepcopy(v) for key, v in value.items()}
    if isinstance(value, list):
        return [fast_deepcopy(v) for v in value]
    return value
