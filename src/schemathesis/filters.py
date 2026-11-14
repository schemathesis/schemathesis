from __future__ import annotations

import difflib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol

from schemathesis.core.errors import IncorrectUsage
from schemathesis.core.statistic import FilterCriterion, UnmatchedFilter
from schemathesis.core.transforms import resolve_pointer

if TYPE_CHECKING:
    from typing import Self

    from schemathesis.schemas import APIOperation


class HasAPIOperation(Protocol):
    operation: APIOperation


@dataclass(slots=True)
class OperationContext:
    """The operation a filter is matched against."""

    operation: APIOperation


MatcherFunc = Callable[[HasAPIOperation], bool]
FilterValue = str | list[str]
RegexValue = str | re.Pattern
ERROR_EXPECTED_AND_REGEX = "Passing expected value and regex simultaneously is not allowed"
ERROR_EMPTY_FILTER = "Filter can not be empty"
ERROR_FILTER_EXISTS = "Filter already exists"


@dataclass(repr=False, frozen=True)
class Matcher:
    func: Callable[..., bool] = field(hash=False, compare=False)
    # Short description, for debugging
    label: str = field(hash=False, compare=False)
    # Matchers compare and hash by this pre-computed value
    _hash: int
    # What this matcher tests, kept so a filter can be named back in the terms the user wrote it.
    attribute: str | None = field(default=None, hash=False, compare=False)
    criterion: object = field(default=None, hash=False, compare=False)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}: {self.label}>"

    @classmethod
    def for_function(cls, func: MatcherFunc) -> Matcher:
        return cls(func, label=func.__name__, _hash=hash(func))

    @classmethod
    def for_value(cls, attribute: str, expected: FilterValue) -> Matcher:
        if isinstance(expected, list):
            func = partial(by_value_list, attribute=attribute, expected=expected)
        else:
            func = partial(by_value, attribute=attribute, expected=expected)
        label = f"{attribute}={expected!r}"
        return cls(func, label=label, _hash=hash(label), attribute=attribute, criterion=expected)

    @classmethod
    def for_regex(cls, attribute: str, regex: RegexValue) -> Matcher:
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
        return self.func(ctx)


def get_operation_attribute(operation: APIOperation, attribute: str) -> str | list[str] | None:
    if attribute == "tag":
        return operation.tags
    if attribute == "operation_id":
        return operation.schema.get_operation_id(operation)
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
        """Whether all matchers match the operation."""
        return all(matcher.match(ctx) for matcher in self.matchers)


@dataclass(slots=True)
class FilterSet:
    """Combines multiple filters to apply inclusion and exclusion rules on API operations."""

    _includes: set[Filter]
    _excludes: set[Filter]
    # The filters as the user wrote them, kept when matching is collapsed into a single function.
    _declared: list[DeclaredFilter] | None = field(default=None, repr=False, compare=False)
    # Whether the operation was turned off explicitly, when matching is collapsed into a single function.
    _deselected: MatcherFunc | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        _includes: set[Filter] | None = None,
        _excludes: set[Filter] | None = None,
        _declared: list[DeclaredFilter] | None = None,
        _deselected: MatcherFunc | None = None,
    ) -> None:
        self._includes = _includes or set()
        self._excludes = _excludes or set()
        self._declared = _declared
        self._deselected = _deselected

    def clone(self) -> Self:
        return self.__class__(
            _includes=self._includes.copy(),
            _excludes=self._excludes.copy(),
            _declared=list(self._declared) if self._declared is not None else None,
            _deselected=self._deselected,
        )

    def applies_to(self, operation: APIOperation) -> bool:
        return self.match(OperationContext(operation=operation))

    def is_explicitly_included(self, operation: APIOperation) -> bool:
        """Whether a filter the user wrote selects this operation."""
        ctx = OperationContext(operation=operation)
        if self._declared is None:
            return any(filter_.match(ctx) for filter_ in self._includes)
        return any(entry.filter.match(ctx) for entry in self._declared if entry.include)

    def is_explicitly_excluded(self, operation: APIOperation) -> bool:
        """Whether a filter the user wrote rejects this operation."""
        ctx = OperationContext(operation=operation)
        if self._deselected is not None and self._deselected(ctx):
            return True
        if self._declared is None:
            return any(filter_.match(ctx) for filter_ in self._excludes)
        return any(entry.filter.match(ctx) for entry in self._declared if not entry.include)

    def match(self, ctx: HasAPIOperation) -> bool:
        """Whether the operation matches no EXCLUDE filter and at least one INCLUDE filter, if any are defined."""
        for filter_ in self._excludes:
            if filter_.match(ctx):
                return False
        if not self._includes:
            return True
        return any(filter_.match(ctx) for filter_ in self._includes)

    def is_empty(self) -> bool:
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

    def with_include(
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
    ) -> Self:
        """A copy of this filter set with a new INCLUDE filter."""
        filter_set = self.clone()
        filter_set.include(
            func,
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
        return filter_set

    def with_exclude(
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
        deprecated: bool = False,
    ) -> Self:
        """A copy of this filter set with a new EXCLUDE filter, optionally excluding deprecated operations too."""
        filter_set = self.clone()
        if deprecated:
            if func is None:
                func = is_deprecated
            else:
                filter_set.exclude(is_deprecated)
        filter_set.exclude(
            func,
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
        return filter_set

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
                # The regex would have to match the expected value anyway, so passing both is redundant
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
    """Attach `attribute` to `target` as a chainable method adding filters via `filter_func`, e.g. `auth.apply_to`."""

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
    return ctx.operation.schema.is_deprecated(ctx.operation)


def parse_expression(expression: str) -> tuple[str, str, Any]:
    expression = expression.strip()

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
    try:
        return pointer, op, json.loads(value)
    except json.JSONDecodeError:
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


SELECTION = "selection"
OPERATIONS = "operations"


@dataclass(frozen=True, slots=True)
class DeclaredFilter:
    """A filter as the user wrote it, kept for reporting the ones that never matched."""

    filter: Filter
    include: bool
    source: str


# Labels and paths share long prefixes, so near-misses need a high bar; methods are a short closed set.
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
        operation_id = operation.schema.get_operation_id(operation)
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
