from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class FilteredCount:
    """Count of total items and those passing filters."""

    total: int
    selected: int

    def __init__(self) -> None:
        self.total = 0
        self.selected = 0


@dataclass(slots=True)
class StatefulInference:
    """Transition counts measured after inference, over the edge population the state machine traverses."""

    # Transitions discovered by inference, as opposed to declared in the schema.
    inferred: int
    total: int
    selected: int


@dataclass(slots=True)
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

    def __init__(self) -> None:
        self.producer_labels = []
        self.consumer_labels = []
        self.resources = 0


@dataclass(slots=True)
class ApiStatistic:
    """Statistics about API operations and inferable stateful transitions."""

    operations: FilteredCount
    transitions: FilteredCount
    resource_pool: ResourcePoolInventory

    def __init__(self) -> None:
        self.operations = FilteredCount()
        self.transitions = FilteredCount()
        self.resource_pool = ResourcePoolInventory()
