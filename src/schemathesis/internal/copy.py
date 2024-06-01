from typing import Any, MutableMapping, Mapping


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


def merge_into(target: MutableMapping[str, Any], source: Mapping[str, Any]) -> None:
    """Merge the contents of the `source` dictionary into the `target` dictionary in-place.

    This function only merges the top-level dictionary, and uses `fast_deepcopy` for the nested values.
    """
    for key, value in source.items():
        target[key] = fast_deepcopy(value)
