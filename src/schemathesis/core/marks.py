"""A lightweight mechanism to attach Schemathesis-specific metadata to test functions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from schemathesis.core import NOT_SET, NotSet

METADATA_ATTR = "_schemathesis_metadata"


@dataclass
class SchemathesisMetadata:
    """Container for all Schemathesis-specific data attached to test functions."""


T = TypeVar("T")


class Mark(Generic[T]):
    """Access to specific attributes in SchemathesisMetadata."""

    def __init__(
        self, *, attr_name: str, default: T | Callable[[], T] | None = None, check: Callable[[T], bool] | None = None
    ) -> None:
        self.attr_name = attr_name
        self._default = default
        self._check = check

    def _get_default(self) -> T | None:
        if callable(self._default):
            return self._default()
        return self._default

    def _check_value(self, value: T) -> bool:
        if self._check is not None:
            return self._check(value)
        return True

    def get(self, func: Callable) -> T | None:
        """Get marker value if it's set."""
        metadata = getattr(func, METADATA_ATTR, None)
        if metadata is None:
            return self._get_default()
        value = getattr(metadata, self.attr_name, NOT_SET)
        if value is NOT_SET:
            return self._get_default()
        assert not isinstance(value, NotSet)
        if self._check_value(value):
            return value
        return self._get_default()

    def set(self, func: Callable, value: T) -> None:
        """Set marker value, creating metadata if needed."""
        if not hasattr(func, METADATA_ATTR):
            setattr(func, METADATA_ATTR, SchemathesisMetadata())
        metadata = getattr(func, METADATA_ATTR)
        setattr(metadata, self.attr_name, value)

    def is_set(self, func: Callable) -> bool:
        """Check if function has metadata with this marker set."""
        metadata = getattr(func, METADATA_ATTR, None)
        if metadata is None:
            return False
        return hasattr(metadata, self.attr_name)
