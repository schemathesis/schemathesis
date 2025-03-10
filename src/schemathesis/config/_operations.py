from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generator

from schemathesis.config._diff_base import DiffBase
from schemathesis.config._error import ConfigError
from schemathesis.config._parameters import ParameterOverride, load_parameters
from schemathesis.core.errors import IncorrectUsage
from schemathesis.filters import FilterSet, expression_to_filter_function

FILTER_ATTRIBUTES = [
    ("name", "name"),
    ("method", "method"),
    ("path", "path"),
    ("tag", "tag"),
    ("operation-id", "operation_id"),
]


@contextmanager
def reraise_filter_error(attr: str) -> Generator:
    try:
        yield
    except IncorrectUsage as exc:
        if str(exc) == "Filter already exists":
            raise ConfigError(
                f"Filter for '{attr}' already exists. You can't simultaneously include and exclude the same thing."
            ) from None
        raise
    except re.error as exc:
        raise ConfigError(
            f"Filter for '{attr}' contains an invalid regular expression: {exc.pattern!r}\n\n  {exc}"
        ) from None


@dataclass
class OperationConfig(DiffBase):
    filter_set: FilterSet
    parameters: dict[str, ParameterOverride]

    __slots__ = (
        "filter_set",
        "parameters",
    )

    def __init__(self, *, filter_set: FilterSet, parameters: dict[str, ParameterOverride] | None = None) -> None:
        self.filter_set = filter_set
        self.parameters = parameters or {}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OperationConfig:
        filter_set = FilterSet()
        for key_suffix, arg_suffix in (("", ""), ("-regex", "_regex")):
            for attr, arg_name in FILTER_ATTRIBUTES:
                key = f"include-{attr}{key_suffix}"
                if key in data:
                    with reraise_filter_error(attr):
                        filter_set.include(**{f"{arg_name}{arg_suffix}": data[key]})
                key = f"exclude-{attr}{key_suffix}"
                if key in data:
                    with reraise_filter_error(attr):
                        filter_set.exclude(**{f"{arg_name}{arg_suffix}": data[key]})
        for key, method in (("include-by", filter_set.include), ("exclude-by", filter_set.exclude)):
            if key in data:
                expression = data[key]
                try:
                    func = expression_to_filter_function(expression)
                    method(func)
                except ValueError:
                    raise ConfigError(f"Invalid filter expression: '{expression}'") from None

        return cls(filter_set=filter_set, parameters=load_parameters(data))
