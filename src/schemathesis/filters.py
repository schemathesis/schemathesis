"""Filtering system that allows users to filter API operations based on certain criteria."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING, Callable, List, Protocol, Union

from .exceptions import UsageError

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
            regex = re.compile(regex)
        func = partial(by_regex, attribute=attribute, regex=regex)
        label = f"{attribute}_regex={repr(regex)}"
        return cls(func, label=label, _hash=hash(label))

    def match(self, ctx: HasAPIOperation) -> bool:
        """Whether matcher matches the given operation."""
        return self.func(ctx)


def get_operation_attribute(operation: APIOperation, attribute: str) -> str | list[str] | None:
    if attribute == "tag":
        return operation.tags
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
        return any(bool(regex.match(entry)) for entry in value)
    return bool(regex.match(value))


@dataclass(repr=False, frozen=True)
class Filter:
    """Match API operations against a list of matchers."""

    matchers: tuple[Matcher, ...]

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

    _includes: set[Filter] = field(default_factory=set)
    _excludes: set[Filter] = field(default_factory=set)

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
    ) -> None:
        matchers = []
        if func is not None:
            matchers.append(Matcher.for_function(func))
        for attribute, expected, regex in (
            ("verbose_name", name, name_regex),
            ("method", method, method_regex),
            ("path", path, path_regex),
            ("tag", tag, tag_regex),
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
        path: FilterValue | None = None,
        path_regex: str | None = None,
    ) -> Callable:
        __tracebackhide__ = True
        filter_func(
            func=func,
            name=name,
            name_regex=name_regex,
            method=method,
            method_regex=method_regex,
            path=path,
            path_regex=path_regex,
        )
        return target

    proxy.__qualname__ = attribute
    proxy.__name__ = attribute

    setattr(target, attribute, proxy)
