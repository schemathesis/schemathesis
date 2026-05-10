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
class ResourcePoolInventory:
    """Resource-pool descriptors discovered from the schema.

    The denominator the analyzer uses to compute "engine exercised M of N known edges".
    Producer/consumer labels are recorded explicitly so runtime synthesised operations
    (e.g. unexpected-method probes) don't inflate coverage ratios.
    """

    # Labels of operations with at least one descriptor that captures into the pool.
    producer_labels: list[str]
    # Labels of operations with at least one resource-bound parameter slot.
    consumer_labels: list[str]
    # Distinct resource types referenced by either side.
    resources: int

    __slots__ = ("producer_labels", "consumer_labels", "resources")

    def __init__(self) -> None:
        self.producer_labels = []
        self.consumer_labels = []
        self.resources = 0


@dataclass
class ApiStatistic:
    """Statistics about API operations and inferable stateful transitions."""

    operations: FilteredCount
    transitions: FilteredCount
    resource_pool: ResourcePoolInventory

    __slots__ = ("operations", "transitions", "resource_pool")

    def __init__(self) -> None:
        self.operations = FilteredCount()
        self.transitions = FilteredCount()
        self.resource_pool = ResourcePoolInventory()
