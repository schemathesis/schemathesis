from __future__ import annotations

from dataclasses import dataclass

from ...models import TestResult, TestResultSet
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...exceptions import OperationSchemaError
    import threading


@dataclass
class RunnerContext:
    """Holds context shared for a test run."""

    data: TestResultSet
    seed: int | None
    stop_event: threading.Event

    __slots__ = ("data", "seed", "stop_event")

    def __init__(self, seed: int | None, stop_event: threading.Event) -> None:
        self.data = TestResultSet(seed=seed)
        self.seed = seed
        self.stop_event = stop_event

    @property
    def is_stopped(self) -> bool:
        return self.stop_event.is_set()

    @property
    def has_all_not_found(self) -> bool:
        """Check if all responses are 404."""
        has_not_found = False
        for entry in self.data.results:
            for check in entry.checks:
                if check.response is not None:
                    if check.response.status_code == 404:
                        has_not_found = True
                    else:
                        # There are non-404 responses, no reason to check any other response
                        return False
        # Only happens if all responses are 404, or there are no responses at all.
        # In the first case, it returns True, for the latter - False
        return has_not_found

    def add_result(self, result: TestResult) -> None:
        self.data.append(result)

    def add_generic_error(self, error: OperationSchemaError) -> None:
        self.data.generic_errors.append(error)

    def add_warning(self, message: str) -> None:
        self.data.add_warning(message)


ALL_NOT_FOUND_WARNING_MESSAGE = "All API responses have a 404 status code. Did you specify the proper API location?"
