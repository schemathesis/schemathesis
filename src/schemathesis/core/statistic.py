from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class FilteredCount:
    total: int
    selected: int

    def __init__(self) -> None:
        self.total = 0
        self.selected = 0


@dataclass(slots=True)
class FilterCriterion:
    """One condition a filter tests."""

    attribute: str
    value: str
    is_regex: bool


@dataclass(slots=True)
class UnmatchedFilter:
    """A filter the user wrote that no operation matched."""

    include: bool
    criteria: list[FilterCriterion]
    suggestion: str | None
    # Where the user wrote it: "selection" for include/exclude, "operations" for a per-operation config block.
    source: str


@dataclass(slots=True)
class StatefulInference:
    """Transition counts after inference, over the edges the state machine traverses."""

    # Transitions discovered by inference, as opposed to declared in the schema.
    inferred: int
    total: int
    selected: int


@dataclass(slots=True)
class ResourcePoolInventory:
    """Resource-pool descriptors from the schema: the N in "exercised M of N known edges".

    Explicit producer/consumer labels keep operations synthesized at runtime, like method probes, out of the ratio.
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
    unmatched_filters: list[UnmatchedFilter]
    # Why nothing is selected when every selected operation is one the specification cannot test.
    unsupported_selection: str | None

    def __init__(self) -> None:
        self.operations = FilteredCount()
        self.transitions = FilteredCount()
        self.resource_pool = ResourcePoolInventory()
        self.unmatched_filters = []
        self.unsupported_selection = None
