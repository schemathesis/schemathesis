from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FilteredCount:
    """Count of total items and those passing filters."""

    total: int
    selected: int

    __slots__ = ("total", "selected")

    def __init__(self) -> None:
        self.total = 0
        self.selected = 0


@dataclass
class ApiStatistic:
    """Statistics about API operations and inferable stateful transitions."""

    operations: FilteredCount
    transitions: FilteredCount

    __slots__ = ("operations", "transitions")

    def __init__(self) -> None:
        self.operations = FilteredCount()
        self.transitions = FilteredCount()
