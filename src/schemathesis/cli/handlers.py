from typing import List, Optional, Set, Tuple

from ..failures import ValidationErrorContext
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
        if check.value == Status.failure:
            key = get_failure_key(check)
            if (check.name, key) not in seen:
                unique_checks.append(check)
                seen.add((check.name, key))
    return unique_checks


def get_failure_key(check: SerializedCheck) -> Optional[str]:
    if isinstance(check.context, ValidationErrorContext):
        # Deduplicate by JSON Schema path. All errors that happened on this sub-schema will be deduplicated
        return "/".join(map(str, check.context.schema_path))
    return check.message
