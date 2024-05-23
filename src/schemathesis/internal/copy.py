from typing import Any, MutableMapping, Mapping


def fast_deepcopy(value: Any) -> Any:
    """A specialized version of `deepcopy` that copies only `dict` and `list`.

    It is on average 3x faster than `deepcopy` and given the amount of calls, it is an important optimization.
    """
    if isinstance(value, dict):
        return {key: fast_deepcopy(v) for key, v in value.items()}
    if isinstance(value, list):
        return [fast_deepcopy(v) for v in value]
    return value


def merge_into(target: MutableMapping[str, Any], source: Mapping[str, Any]) -> None:
    """Merge the contents of the `source` dictionary into the `target` dictionary in-place.

    This function only merges the top-level dictionary, and uses `fast_deepcopy` for the nested values.
    """
    for key, value in source.items():
        target[key] = fast_deepcopy(value)
