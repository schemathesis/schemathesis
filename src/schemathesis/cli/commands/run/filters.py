from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Sequence

import click

from schemathesis.cli.ext.groups import grouped_option
from schemathesis.core.errors import IncorrectUsage
from schemathesis.filters import FilterSet, expression_to_filter_function, is_deprecated


def _with_filter(*, by: str, mode: Literal["include", "exclude"], modifier: Literal["regex"] | None) -> Callable:
    """Generate a CLI option for filtering API operations."""
    param = f"--{mode}-{by}"
    action = "include in" if mode == "include" else "exclude from"
    prop = {
        "operation-id": "ID",
        "name": "Operation name",
    }.get(by, by.capitalize())
    if modifier:
        param += f"-{modifier}"
        prop += " pattern"
    help_text = f"{prop} to {action} testing."
    return grouped_option(
        param,
        help=help_text,
        type=str,
        multiple=modifier is None,
        hidden=True,
    )


_BY_VALUES = ("operation-id", "tag", "name", "method", "path")


def with_filters(command: Callable) -> Callable:
    for by in _BY_VALUES:
        for mode in ("exclude", "include"):
            for modifier in ("regex", None):
                command = _with_filter(by=by, mode=mode, modifier=modifier)(command)  # type: ignore[arg-type]
    return command


@dataclass
class FilterArguments:
    include_path: Sequence[str]
    include_method: Sequence[str]
    include_name: Sequence[str]
    include_tag: Sequence[str]
    include_operation_id: Sequence[str]
    include_path_regex: str | None
    include_method_regex: str | None
    include_name_regex: str | None
    include_tag_regex: str | None
    include_operation_id_regex: str | None

    exclude_path: Sequence[str]
    exclude_method: Sequence[str]
    exclude_name: Sequence[str]
    exclude_tag: Sequence[str]
    exclude_operation_id: Sequence[str]
    exclude_path_regex: str | None
    exclude_method_regex: str | None
    exclude_name_regex: str | None
    exclude_tag_regex: str | None
    exclude_operation_id_regex: str | None

    include_by: str | None
    exclude_by: str | None
    exclude_deprecated: bool

    __slots__ = (
        "include_path",
        "include_method",
        "include_name",
        "include_tag",
        "include_operation_id",
        "include_path_regex",
        "include_method_regex",
        "include_name_regex",
        "include_tag_regex",
        "include_operation_id_regex",
        "exclude_path",
        "exclude_method",
        "exclude_name",
        "exclude_tag",
        "exclude_operation_id",
        "exclude_path_regex",
        "exclude_method_regex",
        "exclude_name_regex",
        "exclude_tag_regex",
        "exclude_operation_id_regex",
        "include_by",
        "exclude_by",
        "exclude_deprecated",
    )

    def into(self) -> FilterSet:
        # Validate unique filter arguments
        for values, arg_name in (
            (self.include_path, "--include-path"),
            (self.include_method, "--include-method"),
            (self.include_name, "--include-name"),
            (self.include_tag, "--include-tag"),
            (self.include_operation_id, "--include-operation-id"),
            (self.exclude_path, "--exclude-path"),
            (self.exclude_method, "--exclude-method"),
            (self.exclude_name, "--exclude-name"),
            (self.exclude_tag, "--exclude-tag"),
            (self.exclude_operation_id, "--exclude-operation-id"),
        ):
            validate_unique_filter(values, arg_name)

        # Convert include/exclude expressions to functions
        include_by_function = _filter_by_expression_to_func(self.include_by, "--include-by")
        exclude_by_function = _filter_by_expression_to_func(self.exclude_by, "--exclude-by")

        filter_set = FilterSet()

        # Apply include filters
        if include_by_function:
            filter_set.include(include_by_function)
        for name_ in self.include_name:
            filter_set.include(name=name_)
        for method in self.include_method:
            filter_set.include(method=method)
        for path in self.include_path:
            filter_set.include(path=path)
        for tag in self.include_tag:
            filter_set.include(tag=tag)
        for operation_id in self.include_operation_id:
            filter_set.include(operation_id=operation_id)
        if (
            self.include_name_regex
            or self.include_method_regex
            or self.include_path_regex
            or self.include_tag_regex
            or self.include_operation_id_regex
        ):
            filter_set.include(
                name_regex=self.include_name_regex,
                method_regex=self.include_method_regex,
                path_regex=self.include_path_regex,
                tag_regex=self.include_tag_regex,
                operation_id_regex=self.include_operation_id_regex,
            )

        # Apply exclude filters
        if exclude_by_function:
            filter_set.exclude(exclude_by_function)
        for name_ in self.exclude_name:
            apply_exclude_filter(filter_set, "name", name=name_)
        for method in self.exclude_method:
            apply_exclude_filter(filter_set, "method", method=method)
        for path in self.exclude_path:
            apply_exclude_filter(filter_set, "path", path=path)
        for tag in self.exclude_tag:
            apply_exclude_filter(filter_set, "tag", tag=tag)
        for operation_id in self.exclude_operation_id:
            apply_exclude_filter(filter_set, "operation-id", operation_id=operation_id)
        for key, value, name in (
            ("name_regex", self.exclude_name_regex, "name-regex"),
            ("method_regex", self.exclude_method_regex, "method-regex"),
            ("path_regex", self.exclude_path_regex, "path-regex"),
            ("tag_regex", self.exclude_tag_regex, "tag-regex"),
            ("operation_id_regex", self.exclude_operation_id_regex, "operation-id-regex"),
        ):
            if value:
                apply_exclude_filter(filter_set, name, **{key: value})

        # Exclude deprecated operations
        if self.exclude_deprecated:
            filter_set.exclude(is_deprecated)

        return filter_set


def apply_exclude_filter(filter_set: FilterSet, option_name: str, **kwargs: Any) -> None:
    """Apply an exclude filter with proper error handling."""
    try:
        filter_set.exclude(**kwargs)
    except IncorrectUsage as e:
        if str(e) == "Filter already exists":
            raise click.UsageError(
                f"Filter for {option_name} already exists. You can't simultaneously include and exclude the same thing."
            ) from None
        raise click.UsageError(str(e)) from None


def validate_unique_filter(values: Sequence[str], arg_name: str) -> None:
    if len(values) != len(set(values)):
        duplicates = ",".join(sorted({value for value in values if values.count(value) > 1}))
        raise click.UsageError(f"Duplicate values are not allowed for `{arg_name}`: {duplicates}")


def _filter_by_expression_to_func(value: str | None, arg_name: str) -> Callable | None:
    if value:
        try:
            return expression_to_filter_function(value)
        except ValueError:
            raise click.UsageError(f"Invalid expression for {arg_name}: {value}") from None
    return None
