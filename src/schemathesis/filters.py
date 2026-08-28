"""Filtering system that allows users to filter API operations based on certain criteria."""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Protocol

from schemathesis.core.errors import IncorrectUsage
from schemathesis.core.statistic import FilterCriterion, UnmatchedFilter
from schemathesis.core.transforms import resolve_pointer

if TYPE_CHECKING:
    from typing_extensions import Self

    from schemathesis.schemas import APIOperation


class HasAPIOperation(Protocol):
    operation: APIOperation


MatcherFunc = Callable[[HasAPIOperation], bool]
FilterValue = str | list[str]
RegexValue = str | re.Pattern
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
    # What this matcher tests, kept so a filter can be named back in the terms the user wrote it.
    attribute: str | None = field(default=None, hash=False, compare=False)
    criterion: object = field(default=None, hash=False, compare=False)

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
        label = f"{attribute}={expected!r}"
        return cls(func, label=label, _hash=hash(label), attribute=attribute, criterion=expected)

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
        label = f"{attribute}_regex={regex!r}"
        return cls(func, label=label, _hash=hash(label), attribute=f"{attribute}_regex", criterion=regex.pattern)

    def match(self, ctx: HasAPIOperation) -> bool:
        """Whether matcher matches the given operation."""
        return self.func(ctx)


def get_operation_attribute(operation: APIOperation, attribute: str) -> str | list[str] | None:
    if attribute == "tag":
        return operation.tags
    if attribute == "operation_id":
        return operation.schema.get_operation_id(operation)
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


@dataclass(repr=False, frozen=True, slots=True)
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


@dataclass(slots=True)
class FilterSet:
    """Combines multiple filters to apply inclusion and exclusion rules on API operations."""

    _includes: set[Filter]
    _excludes: set[Filter]
    # The filters as the user wrote them, kept when matching is collapsed into a single function.
    _declared: list[DeclaredFilter] | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        _includes: set[Filter] | None = None,
        _excludes: set[Filter] | None = None,
        _declared: list[DeclaredFilter] | None = None,
    ) -> None:
        self._includes = _includes or set()
        self._excludes = _excludes or set()
        self._declared = _declared

    def clone(self) -> Self:
        return self.__class__(
            _includes=self._includes.copy(),
            _excludes=self._excludes.copy(),
            _declared=list(self._declared) if self._declared is not None else None,
        )

    def applies_to(self, operation: APIOperation) -> bool:
        return self.match(SimpleNamespace(operation=operation))

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

    def clear(self) -> None:
        self._includes.clear()
        self._excludes.clear()

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
            ("label", name, name_regex),
            ("method", method, method_regex),
            ("path", path, path_regex),
            ("tag", tag, tag_regex),
            ("operation_id", operation_id, operation_id_regex),
        ):
            if expected is not None and regex is not None:
                # To match anything the regex should match the expected value, hence passing them together is useless
                raise IncorrectUsage(ERROR_EXPECTED_AND_REGEX)
            if expected is not None:
                if attribute == "method":
                    expected = _normalize_method(expected)
                matchers.append(Matcher.for_value(attribute, expected))
            if regex is not None:
                matchers.append(Matcher.for_regex(attribute, regex))

        if not matchers:
            raise IncorrectUsage(ERROR_EMPTY_FILTER)
        filter_ = Filter(matchers=tuple(matchers))
        if filter_ in self._includes or filter_ in self._excludes:
            raise IncorrectUsage(ERROR_FILTER_EXISTS)
        if include:
            self._includes.add(filter_)
        else:
            self._excludes.add(filter_)


def _normalize_method(value: FilterValue) -> FilterValue:
    if isinstance(value, list):
        return [item.upper() for item in value]
    return value.upper()


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
    pointer, op, value = parse_expression(expression)

    if op == "==":

        def filter_function(ctx: HasAPIOperation) -> bool:
            definition = ctx.operation.definition.raw
            resolved = resolve_pointer(definition, pointer)
            return resolved == value
    else:

        def filter_function(ctx: HasAPIOperation) -> bool:
            definition = ctx.operation.definition.raw
            resolved = resolve_pointer(definition, pointer)
            return resolved != value

    return filter_function


# Labels and paths share long prefixes, so near-misses need a high bar; methods are a short closed set.
SELECTION = "selection"
OPERATIONS = "operations"


@dataclass(frozen=True, slots=True)
class DeclaredFilter:
    """A filter as the user wrote it, kept for reporting the ones that never matched."""

    filter: Filter
    include: bool
    source: str


SUGGESTION_CUTOFF = 0.8
METHOD_SUGGESTION_CUTOFF = 0.6


def _criteria(filter_: Filter) -> list[FilterCriterion] | None:
    """The conditions a filter tests, or `None` when it is backed by a function rather than values."""
    criteria = []
    for matcher in filter_.matchers:
        attribute = matcher.attribute
        if attribute is None or not isinstance(matcher.criterion, str):
            return None
        is_regex = attribute.endswith("_regex")
        criteria.append(
            FilterCriterion(
                attribute=attribute[: -len("_regex")] if is_regex else attribute,
                value=matcher.criterion,
                is_regex=is_regex,
            )
        )
    return criteria


class FilterUsage:
    """Records which filters ever matched, so the ones that never did can be reported."""

    __slots__ = ("_pending", "_candidates")

    def __init__(self, filter_set: FilterSet) -> None:
        # Keyed by direction too: an include and an exclude on the same criteria are equal as filters.
        self._pending: dict[tuple[Filter, bool, str], list[FilterCriterion]] = {}
        self._candidates: dict[str, set[str]] = {}
        declared = filter_set._declared
        if declared is None:
            declared = [DeclaredFilter(filter_, True, SELECTION) for filter_ in filter_set._includes]
            declared += [DeclaredFilter(filter_, False, SELECTION) for filter_ in filter_set._excludes]
        for entry in declared:
            criteria = _criteria(entry.filter)
            if criteria is not None:
                self._pending[(entry.filter, entry.include, entry.source)] = criteria

    def record(self, ctx: HasAPIOperation) -> None:
        if not self._pending:
            return
        for key in [key for key in self._pending if key[0].match(ctx)]:
            del self._pending[key]
        operation = ctx.operation
        self._candidates.setdefault("label", set()).add(operation.label)
        self._candidates.setdefault("path", set()).add(operation.path)
        self._candidates.setdefault("method", set()).add(operation.method.upper())
        self._candidates.setdefault("tag", set()).update(operation.tags or ())
        operation_id = operation.definition.raw.get("operationId")
        if operation_id is not None:
            self._candidates.setdefault("operation_id", set()).add(operation_id)

    def results(self) -> list[UnmatchedFilter]:
        return sorted(
            (
                UnmatchedFilter(
                    include=include,
                    criteria=criteria,
                    suggestion=self._suggestion_for(criteria),
                    source=source,
                )
                for (_, include, source), criteria in self._pending.items()
            ),
            key=lambda entry: (
                entry.source,
                not entry.include,
                [(item.attribute, item.value) for item in entry.criteria],
            ),
        )

    def _suggestion_for(self, criteria: list[FilterCriterion]) -> str | None:
        """The closest real operation value, for a filter that tests exactly one literal."""
        if len(criteria) != 1:
            return None
        criterion = criteria[0]
        if criterion.is_regex:
            return None
        # Operation labels share a method prefix, which alone clears a lower cutoff and suggests any of them.
        candidates = {
            candidate.lower(): candidate for candidate in sorted(self._candidates.get(criterion.attribute, ()))
        }
        cutoff = METHOD_SUGGESTION_CUTOFF if criterion.attribute == "method" else SUGGESTION_CUTOFF
        matches = difflib.get_close_matches(criterion.value.lower(), list(candidates), n=1, cutoff=cutoff)
        return candidates[matches[0]] if matches else None
