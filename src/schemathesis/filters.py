import enum
from typing import Any, Callable, List, Optional

import attr

# Indicates the most common place where filters are applied.
# Other scopes are spec-specific and their filters may be applied earlier to avoid expensive computations
DEFAULT_SCOPE = None


class FilterResult(enum.Enum):
    """The result of a single filter call.

    This functionality is implemented as a separate enum and not a simple boolean to provide a more descriptive API.
    """

    INCLUDED = enum.auto()
    EXCLUDED = enum.auto()

    @property
    def is_included(self) -> bool:
        return self == FilterResult.INCLUDED

    @property
    def is_excluded(self) -> bool:
        return self == FilterResult.EXCLUDED

    def __bool__(self) -> bool:
        return self.is_included

    def __and__(self, other: "FilterResult") -> "FilterResult":
        if self.is_excluded or other.is_excluded:
            return FilterResult.EXCLUDED
        return self


@attr.s(slots=True)
class BaseFilter:
    func: Callable[..., bool] = attr.ib()
    scope: Optional[str] = attr.ib(default=DEFAULT_SCOPE)

    def apply(self, item: Any) -> FilterResult:
        raise NotImplementedError


@attr.s(slots=True)
class Include(BaseFilter):
    def apply(self, item: Any) -> FilterResult:
        if self.func(item):
            return FilterResult.INCLUDED
        return FilterResult.EXCLUDED


@attr.s(slots=True)
class Exclude(BaseFilter):
    def apply(self, item: Any) -> FilterResult:
        if self.func(item):
            return FilterResult.EXCLUDED
        return FilterResult.INCLUDED


def evaluate_filters(filters: List[BaseFilter], item: Any, scope: Optional[str] = DEFAULT_SCOPE) -> FilterResult:
    """Decide whether the given item passes the filters."""
    # Lazily apply filters that match the given scope
    matching_filters = filter(lambda f: f.scope == scope, filters)
    outcomes = map(lambda f: f.apply(item), matching_filters)
    # If any filter will exclude the item, then the process short-circuits without evaluating all filters
    if all(outcomes):
        return FilterResult.INCLUDED
    return FilterResult.EXCLUDED


def is_excluded(filters: List[BaseFilter], item: Any, scope: Optional[str] = DEFAULT_SCOPE) -> bool:
    """Whether the given filters exclude the item."""
    return evaluate_filters(filters, item, scope).is_excluded
