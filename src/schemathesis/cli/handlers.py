from typing import List, Optional, Set, Tuple

from ..models import Status
from ..runner import events
from ..runner.serialization import SerializedCheck
from .context import ExecutionContext


class EventHandler:
    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        raise NotImplementedError

    def shutdown(self) -> None:
        # Do nothing by default
        pass


def get_unique_failures(checks: List[SerializedCheck]) -> List[SerializedCheck]:
    """Return only unique checks that should be displayed in the output."""
    seen: Set[Tuple[str, Optional[str]]] = set()
    unique_checks = []
    for check in reversed(checks):
        # There are also could be checks that didn't fail
        if check.value == Status.failure and (check.name, check.message) not in seen:
            unique_checks.append(check)
            seen.add((check.name, check.message))
    return unique_checks
