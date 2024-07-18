"""Filtering system that allows users to filter API operations based on certain criteria."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Callable, List, Protocol, Union

from .exceptions import UsageError
from .types import Filter as FilterType
from .types import NotSet

if TYPE_CHECKING:
    from .models import APIOperation


class HasAPIOperation(Protocol):
    operation: APIOperation


MatcherFunc = Callable[[HasAPIOperation], bool]
FilterValue = Union[str, List[str]]
RegexValue = Union[str, re.Pattern]
ERROR_EXPECTED_AND_REGEX = "Passing expected value and regex simultaneously is not allowed"
ERROR_EMPTY_FILTER = "Filter can not be empty"
ERROR_FILTER_EXISTS = "Filter already exists"


@dataclass(repr=False, frozen=True)
class Matcher:
    """Encapsulates matching logic by various criteria."""

    func: Callable[..., bool] = field(hash=False, compare=False)
    # A short description of a matcher. Primarily exists for debugging purposes
    label: str = field(hash=False, compare=False)
    # Compare & hash matchers by a pre-computed hash value
    _hash: int

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}: {self.label}>"

    @classmethod
    def for_function(cls, func: MatcherFunc) -> Matcher:
        """Matcher that uses the given function for matching operations."""
        return cls(func, label=func.__name__, _hash=hash(func))

    @classmethod
    def for_value(cls, attribute: str, expected: FilterValue) -> Matcher:
        """Matcher that checks whether the specified attribute has the expected value."""
        if isinstance(expected, list):
            func = partial(by_value_list, attribute=attribute, expected=expected)
        else:
            func = partial(by_value, attribute=attribute, expected=expected)
        label = f"{attribute}={repr(expected)}"
        return cls(func, label=label, _hash=hash(label))

    @classmethod
    def for_regex(cls, attribute: str, regex: RegexValue) -> Matcher:
        """Matcher that checks whether the specified attribute has the provided regex."""
        if isinstance(regex, str):
            flags: re.RegexFlag | int
            if attribute == "method":
                flags = re.IGNORECASE
            else:
                flags = 0
            regex = re.compile(regex, flags=flags)
        func = partial(by_regex, attribute=attribute, regex=regex)
        label = f"{attribute}_regex={repr(regex)}"
        return cls(func, label=label, _hash=hash(label))

    def match(self, ctx: HasAPIOperation) -> bool:
        """Whether matcher matches the given operation."""
        return self.func(ctx)


def get_operation_attribute(operation: APIOperation, attribute: str) -> str | list[str] | None:
    if attribute == "tag":
        return operation.tags
    if attribute == "operation_id":
        return operation.definition.raw.get("operationId")
    # Just uppercase `method`
    value = getattr(operation, attribute)
    if attribute == "method":
        value = value.upper()
    return value


def by_value(ctx: HasAPIOperation, attribute: str, expected: str) -> bool:
    value = get_operation_attribute(ctx.operation, attribute)
    if value is None:
        return False
    if isinstance(value, list):
        return any(entry == expected for entry in value)
    return value == expected


def by_value_list(ctx: HasAPIOperation, attribute: str, expected: list[str]) -> bool:
    value = get_operation_attribute(ctx.operation, attribute)
    if value is None:
        return False
    if isinstance(value, list):
        return any(entry in expected for entry in value)
    return value in expected


def by_regex(ctx: HasAPIOperation, attribute: str, regex: re.Pattern) -> bool:
    value = get_operation_attribute(ctx.operation, attribute)
    if value is None:
        return False
    if isinstance(value, list):
        return any(bool(regex.search(entry)) for entry in value)
    return bool(regex.search(value))


@dataclass(repr=False, frozen=True)
class Filter:
    """Match API operations against a list of matchers."""

    matchers: tuple[Matcher, ...]

    __slots__ = ("matchers",)

    def __repr__(self) -> str:
        inner = " && ".join(matcher.label for matcher in self.matchers)
        return f"<{self.__class__.__name__}: [{inner}]>"

    def match(self, ctx: HasAPIOperation) -> bool:
        """Whether the operation matches the filter.

        Returns `True` only if all matchers matched.
        """
        return all(matcher.match(ctx) for matcher in self.matchers)


@dataclass
class FilterSet:
    """Combines multiple filters to apply inclusion and exclusion rules on API operations."""

    _includes: set[Filter]
    _excludes: set[Filter]

    __slots__ = ("_includes", "_excludes")

    def __init__(self, _includes: set[Filter] | None = None, _excludes: set[Filter] | None = None) -> None:
        self._includes = _includes or set()
        self._excludes = _excludes or set()

    def clone(self) -> FilterSet:
        return FilterSet(_includes=self._includes.copy(), _excludes=self._excludes.copy())

    def merge(self, other: FilterSet) -> FilterSet:
        def _merge(lhs: set[Filter], rhs: set[Filter]) -> set[Filter]:
            result = lhs.copy()
            for new in rhs:
                for old in lhs:
                    for new_matcher in new.matchers:
                        for old_matcher in old.matchers:
                            if "=" in new_matcher.label and "=" in old_matcher.label:
                                if new_matcher.label.split("=")[0] == old_matcher.label.split("=")[0]:
                                    result.remove(old)
                result.add(new)
            return result

        return FilterSet(
            _includes=_merge(self._includes, other._includes), _excludes=_merge(self._excludes, other._excludes)
        )

    def apply_to(self, operations: list[APIOperation]) -> list[APIOperation]:
        """Get a filtered list of the given operations that match the filters."""
        return [operation for operation in operations if self.match(SimpleNamespace(operation=operation))]

    def match(self, ctx: HasAPIOperation) -> bool:
        """Determines whether the given operation should be included based on the defined filters.

        Returns True if the operation:
          - matches at least one INCLUDE filter OR no INCLUDE filters defined;
          - does not match any EXCLUDE filter;
        False otherwise.
        """
        # Exclude early if the operation is excluded by at least one EXCLUDE filter
        for filter_ in self._excludes:
            if filter_.match(ctx):
                return False
        if not self._includes:
            # No includes - nothing to filter out, include the operation
            return True
        # Otherwise check if the operation is included by at least one INCLUDE filter
        return any(filter_.match(ctx) for filter_ in self._includes)

    def is_empty(self) -> bool:
        """Whether the filter set does not contain any filters."""
        return not self._includes and not self._excludes

    def include(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: RegexValue | None = None,
        method: FilterValue | None = None,
        method_regex: RegexValue | None = None,
        path: FilterValue | None = None,
        path_regex: RegexValue | None = None,
        tag: FilterValue | None = None,
        tag_regex: RegexValue | None = None,
        operation_id: FilterValue | None = None,
        operation_id_regex: RegexValue | None = None,
    ) -> None:
        """Add a new INCLUDE filter."""
        self._add_filter(
            True,
            func=func,
            name=name,
            name_regex=name_regex,
            method=method,
            method_regex=method_regex,
            path=path,
            path_regex=path_regex,
            tag=tag,
            tag_regex=tag_regex,
            operation_id=operation_id,
            operation_id_regex=operation_id_regex,
        )

    def exclude(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: RegexValue | None = None,
        method: FilterValue | None = None,
        method_regex: RegexValue | None = None,
        path: FilterValue | None = None,
        path_regex: RegexValue | None = None,
        tag: FilterValue | None = None,
        tag_regex: RegexValue | None = None,
        operation_id: FilterValue | None = None,
        operation_id_regex: RegexValue | None = None,
    ) -> None:
        """Add a new EXCLUDE filter."""
        self._add_filter(
            False,
            func=func,
            name=name,
            name_regex=name_regex,
            method=method,
            method_regex=method_regex,
            path=path,
            path_regex=path_regex,
            tag=tag,
            tag_regex=tag_regex,
            operation_id=operation_id,
            operation_id_regex=operation_id_regex,
        )

    def _add_filter(
        self,
        include: bool,
        *,
        func: MatcherFunc | None = None,
        name: FilterValue | None = None,
        name_regex: RegexValue | None = None,
        method: FilterValue | None = None,
        method_regex: RegexValue | None = None,
        path: FilterValue | None = None,
        path_regex: RegexValue | None = None,
        tag: FilterValue | None = None,
        tag_regex: RegexValue | None = None,
        operation_id: FilterValue | None = None,
        operation_id_regex: RegexValue | None = None,
    ) -> None:
        matchers = []
        if func is not None:
            matchers.append(Matcher.for_function(func))
        for attribute, expected, regex in (
            ("verbose_name", name, name_regex),
            ("method", method, method_regex),
            ("path", path, path_regex),
            ("tag", tag, tag_regex),
            ("operation_id", operation_id, operation_id_regex),
        ):
            if expected is not None and regex is not None:
                # To match anything the regex should match the expected value, hence passing them together is useless
                raise UsageError(ERROR_EXPECTED_AND_REGEX)
            if expected is not None:
                matchers.append(Matcher.for_value(attribute, expected))
            if regex is not None:
                matchers.append(Matcher.for_regex(attribute, regex))

        if not matchers:
            raise UsageError(ERROR_EMPTY_FILTER)
        filter_ = Filter(matchers=tuple(matchers))
        if filter_ in self._includes or filter_ in self._excludes:
            raise UsageError(ERROR_FILTER_EXISTS)
        if include:
            self._includes.add(filter_)
        else:
            self._excludes.add(filter_)


def attach_filter_chain(
    target: Callable,
    attribute: str,
    filter_func: Callable[..., None],
) -> None:
    """Attach a filtering function to an object, which allows chaining of filter criteria.

    For example:

    >>> def auth(): ...
    >>> filter_set = FilterSet()
    >>> attach_filter_chain(auth, "apply_to", filter_set.include)
    >>> auth.apply_to(method="GET", path="/users/")

    This will add a new `apply_to` method to `auth` that matches only the `GET /users/` operation.
    """

    def proxy(
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        tag: FilterValue | None = None,
        tag_regex: RegexValue | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
        operation_id: FilterValue | None = None,
        operation_id_regex: RegexValue | None = None,
    ) -> Callable:
        __tracebackhide__ = True
        filter_func(
            func=func,
            name=name,
            name_regex=name_regex,
            method=method,
            method_regex=method_regex,
            tag=tag,
            tag_regex=tag_regex,
            path=path,
            path_regex=path_regex,
            operation_id=operation_id,
            operation_id_regex=operation_id_regex,
        )
        return target

    proxy.__qualname__ = attribute
    proxy.__name__ = attribute

    setattr(target, attribute, proxy)


def is_deprecated(ctx: HasAPIOperation) -> bool:
    return ctx.operation.definition.raw.get("deprecated") is True


def filter_set_from_components(
    *,
    include: bool,
    method: FilterType | None = None,
    endpoint: FilterType | None = None,
    tag: FilterType | None = None,
    operation_id: FilterType | None = None,
    skip_deprecated_operations: bool | None | NotSet = None,
    parent: FilterSet | None = None,
) -> FilterSet:
    def _is_defined(x: FilterType | None) -> bool:
        return x is not None and not isinstance(x, NotSet)

    def _prepare_filter(filter_: FilterType | None) -> RegexValue | None:
        if filter_ is None or isinstance(filter_, NotSet):
            return None
        if isinstance(filter_, str):
            return filter_
        return "|".join(f"({f})" for f in filter_)

    new = FilterSet()

    if _is_defined(method) or _is_defined(endpoint) or _is_defined(tag) or _is_defined(operation_id):
        new._add_filter(
            include,
            method_regex=_prepare_filter(method),
            path_regex=_prepare_filter(endpoint),
            tag_regex=_prepare_filter(tag),
            operation_id_regex=_prepare_filter(operation_id),
        )
    if skip_deprecated_operations is True and not any(
        matcher.label == is_deprecated.__name__ for exclude_ in new._excludes for matcher in exclude_.matchers
    ):
        new.exclude(func=is_deprecated)
    # Merge with the parent filter set
    if parent is not None:
        for include_ in parent._includes:
            matchers = include_.matchers
            ids = []
            for idx, matcher in enumerate(matchers):
                label = matcher.label
                if (
                    (not isinstance(method, NotSet) and label.startswith("method_regex="))
                    or (not isinstance(endpoint, NotSet) and label.startswith("path_regex="))
                    or (not isinstance(tag, NotSet) and matcher.label.startswith("tag_regex="))
                    or (not isinstance(operation_id, NotSet) and matcher.label.startswith("operation_id_regex="))
                ):
                    ids.append(idx)
            if ids:
                matchers = tuple(matcher for idx, matcher in enumerate(matchers) if idx not in ids)
            if matchers:
                if new._includes:
                    existing = new._includes.pop()
                    matchers = existing.matchers + matchers
                new._includes.add(Filter(matchers=matchers))
        for exclude_ in parent._excludes:
            matchers = exclude_.matchers
            ids = []
            for idx, matcher in enumerate(exclude_.matchers):
                if skip_deprecated_operations is False and matcher.label == is_deprecated.__name__:
                    ids.append(idx)
            if ids:
                matchers = tuple(matcher for idx, matcher in enumerate(matchers) if idx not in ids)
            if matchers:
                new._excludes.add(exclude_)
    return new


def parse_expression(expression: str) -> tuple[str, str, Any]:
    expression = expression.strip()

    # Find the operator
    for op in ("==", "!="):
        try:
            pointer, value = expression.split(op, 1)
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"Invalid expression: {expression}")

    pointer = pointer.strip()
    value = value.strip()
    if not pointer or not value:
        raise ValueError(f"Invalid expression: {expression}")
    # Parse the JSON value
    try:
        return pointer, op, json.loads(value)
    except json.JSONDecodeError:
        # If it's not valid JSON, treat it as a string
        return pointer, op, value


def expression_to_filter_function(expression: str) -> Callable[[HasAPIOperation], bool]:
    from .specs.openapi.references import resolve_pointer

    pointer, op, value = parse_expression(expression)

    if op == "==":

        def filter_function(ctx: HasAPIOperation) -> bool:
            definition = ctx.operation.definition.resolved
            resolved = resolve_pointer(definition, pointer)
            return resolved == value
    else:

        def filter_function(ctx: HasAPIOperation) -> bool:
            definition = ctx.operation.definition.resolved
            resolved = resolve_pointer(definition, pointer)
            return resolved != value

    return filter_function
