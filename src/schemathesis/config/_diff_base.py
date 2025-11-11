from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import TypeVar

T = TypeVar("T", bound="DiffBase")


@dataclass
class DiffBase:
    def __repr__(self) -> str:
        """Show only the fields that differ from the default."""
        assert is_dataclass(self)
        default = self.__class__()
        diffs = []
        for field in fields(self):
            name = field.name
            if name.startswith("_") and name not in ("_seed", "_filter_set"):
                continue
            current_value = getattr(self, name)
            default_value = getattr(default, name)
            if name == "_seed":
                name = "seed"
            if name == "_filter_set":
                name = "filter_set"
            if name == "rate_limit" and current_value is not None:
                assert hasattr(self, "_rate_limit")
                current_value = self._rate_limit
            if self._has_diff(current_value, default_value):
                diffs.append(f"{name}={self._diff_repr(current_value, default_value)}")
        return f"{self.__class__.__name__}({', '.join(diffs)})"

    def _has_diff(self, value: object, default: object) -> bool:
        if is_dataclass(value):
            return repr(value) != repr(default)
        if isinstance(value, list) and isinstance(default, list):
            if len(value) != len(default):
                return True
            return any(self._has_diff(v, d) for v, d in zip(value, default, strict=False))
        if isinstance(value, dict) and isinstance(default, dict):
            if set(value.keys()) != set(default.keys()):
                return True
            return any(self._has_diff(value[k], default[k]) for k in value)
        return value != default

    def _diff_repr(self, value: object, default: object) -> str:
        if is_dataclass(value):
            # If the nested object is a dataclass, recursively show its diff.
            return repr(value)
        if isinstance(value, list) and isinstance(default, list):
            diff_items = []
            # Compare items pairwise.
            for v, d in zip(value, default, strict=False):
                if self._has_diff(v, d):
                    diff_items.append(self._diff_repr(v, d))
            # Include any extra items in value.
            if len(value) > len(default):
                diff_items.extend(_repr(item) for item in value[len(default) :])
            return f"[{', '.join(_repr(item) for item in value)}]"
        if isinstance(value, dict) and isinstance(default, dict):
            diff_items = []
            for k, v in value.items():
                d = default.get(k)
                if self._has_diff(v, d):
                    diff_items.append(f"{k!r}: {self._diff_repr(v, d)}")
            return f"{{{', '.join(diff_items)}}}"
        return repr(value)

    @classmethod
    def from_hierarchy(cls, configs: list[T]) -> T:
        # This config will accumulate "merged" config options
        if len(configs) == 1:
            return configs[0]
        output = cls()
        for option in cls.__slots__:  # type: ignore[attr-defined]
            if option.startswith("_"):
                continue
            default = getattr(output, option)
            if hasattr(default, "__dataclass_fields__"):
                # Sub-configs require merging of nested config options
                sub_configs = [getattr(config, option) for config in configs]
                merged = type(default).from_hierarchy(sub_configs)
                setattr(output, option, merged)
            else:
                # Primitive config options can be compared directly and do not
                # require merging of nested options
                for config in configs:
                    current = getattr(config, option)
                    if current != default:
                        setattr(output, option, current)
                        # As we go from the highest priority to the lowest one,
                        # we can just stop on the first non-default value
                        break
        return output  # type: ignore[return-value]


def _repr(item: object) -> str:
    if callable(item) and hasattr(item, "__name__"):
        return f"<function {item.__name__}>"

    return repr(item)
