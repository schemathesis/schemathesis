from __future__ import annotations

import inspect
from collections.abc import Iterable
from dataclasses import fields, is_dataclass
from itertools import starmap
from typing import ClassVar, TypeVar

T = TypeVar("T", bound="DiffBase")


def _required_parameters(cls: type) -> set[str]:
    return {
        name
        for name, parameter in inspect.signature(cls).parameters.items()
        if parameter.default is inspect.Parameter.empty and parameter.kind is not parameter.VAR_KEYWORD
    }


# Not a dataclass: subclasses are, and a base field would land before their own required ones.
class DiffBase:
    # Options that came from a config file key or an `update()` argument. Empty for configs built
    # directly in Python, where an explicit value cannot be told apart from a default one.
    _source_keys: frozenset[str] = frozenset()

    # Config-file keys whose name differs from the option they set.
    _key_aliases: ClassVar[dict[str, str]] = {}

    def _mark_source_keys(self: T, keys: Iterable[str]) -> T:
        normalized = (key.replace("-", "_") for key in keys)
        self._source_keys = self._source_keys | {self._key_aliases.get(key, key) for key in normalized}
        return self

    def _apply(self, **values: object) -> None:
        """Set every provided option and record it as coming from an explicit source."""
        provided = [name for name, value in values.items() if value is not None]
        for name in provided:
            setattr(self, name, values[name])
        self._mark_source_keys(provided)

    def __repr__(self) -> str:
        """Show only the fields that differ from the default."""
        # A section with required arguments has no argument-free default, so it is built from those values
        # and they are always shown.
        required = _required_parameters(self.__class__)
        default = self.__class__(**{name: getattr(self, name) for name in required})
        diffs = []
        # Every subclass is a dataclass; this base only carries the shared behaviour.
        for field in fields(self):  # type: ignore[arg-type]
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
            if name in required or self._has_diff(current_value, default_value):
                diffs.append(f"{name}={self._diff_repr(current_value, default_value)}")
        return f"{self.__class__.__name__}({', '.join(diffs)})"

    def _has_diff(self, value: object, default: object) -> bool:
        if is_dataclass(value):
            return repr(value) != repr(default)
        if isinstance(value, list) and isinstance(default, list):
            if len(value) != len(default):
                return True
            return any(starmap(self._has_diff, zip(value, default, strict=False)))
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
                    source_keys = config._source_keys
                    # A config decides an option when its source named it. Configs built in Python
                    # have no source, so any non-default value counts as a decision.
                    decides = option in source_keys if source_keys else current != default
                    if decides:
                        setattr(output, option, current)
                        # As we go from the highest priority to the lowest one,
                        # we can stop at the first config that sets the option
                        break
        return output  # type: ignore[return-value]


def _repr(item: object) -> str:
    if callable(item) and hasattr(item, "__name__"):
        return f"<function {item.__name__}>"

    return repr(item)
