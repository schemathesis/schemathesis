from __future__ import annotations

import inspect
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from ..models import Case
    from ..transports.responses import GenericResponse


CheckFunction = Callable[["CheckContext", "GenericResponse", "Case"], Optional[bool]]


@dataclass
class CheckContext:
    """Context for Schemathesis checks.

    Provides access to broader test execution data beyond individual test cases.
    """


def wrap_check(check: Callable) -> CheckFunction:
    """Make older checks compatible with the new signature."""
    signature = inspect.signature(check)
    parameters = len(signature.parameters)

    if parameters == 3:
        # New style check, return as is
        return check

    if parameters == 2:
        # Old style check, wrap it
        warnings.warn(
            f"The check function '{check.__name__}' uses an outdated signature. "
            "Please update it to accept 'ctx' as the first argument: "
            "(ctx: CheckContext, response: GenericResponse, case: Case) -> Optional[bool]",
            DeprecationWarning,
            stacklevel=2,
        )

        def wrapper(_: CheckContext, response: GenericResponse, case: Case) -> Optional[bool]:
            return check(response, case)

        wrapper.__name__ = check.__name__

        return wrapper

    raise ValueError(f"Invalid check function signature. Expected 2 or 3 parameters, got {parameters}")
