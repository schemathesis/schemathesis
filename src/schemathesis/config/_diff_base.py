from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import Any, TypeVar

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
            if name.startswith("_"):
                continue
            current_value = getattr(self, name)
            default_value = getattr(default, name)
            if self._has_diff(current_value, default_value):
                diffs.append(f"{name}={self._diff_repr(current_value, default_value)}")
        return f"{self.__class__.__name__}({', '.join(diffs)})"

    def _has_diff(self, value: object, default: object) -> bool:
        if is_dataclass(value):
            return repr(value) != repr(default)
        if isinstance(value, list) and isinstance(default, list):
            if len(value) != len(default):
                return True
            return any(self._has_diff(v, d) for v, d in zip(value, default))
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
            for v, d in zip(value, default):
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
    def get_explicit_attrs(cls, keys: set[str]) -> set[str]:
        return {field.name for field in fields(cls) if not field.name.startswith("_")} & {
            key.replace("-", "_") for key in keys
        }

    def merge(self, other: T) -> T:
        kwargs: dict[str, Any] = {}
        for field in fields(other):
            field_name = field.name
            if field_name.startswith("_"):
                continue
            if field_name in self._explicit_attrs:  # type: ignore[attr-defined]
                kwargs[field_name] = getattr(self, field_name)
            else:
                kwargs[field_name] = getattr(other, field_name)
        merged_explicit = other._explicit_attrs | self._explicit_attrs  # type: ignore[attr-defined]
        return type(other)(**kwargs, _explicit_attrs=merged_explicit)  # type: ignore[call-arg]


def _repr(item: object) -> str:
    if callable(item) and hasattr(item, "__name__"):
        return f"<function {item.__name__}>"

    return repr(item)
