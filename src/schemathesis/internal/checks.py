from __future__ import annotations

import inspect
import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from requests.auth import HTTPDigestAuth
    from requests.structures import CaseInsensitiveDict

    from .._override import CaseOverride
    from ..models import Case
    from ..transports.responses import GenericResponse
    from ..types import RawAuth


CheckFunction = Callable[["CheckContext", "GenericResponse", "Case"], Optional[bool]]


@dataclass
class NegativeDataRejectionConfig:
    # 5xx will pass through
    allowed_statuses: list[str] = field(default_factory=lambda: ["400", "401", "403", "404", "422", "5xx"])


@dataclass
class PositiveDataAcceptanceConfig:
    allowed_statuses: list[str] = field(default_factory=lambda: ["2xx", "401", "403", "404"])


@dataclass
class CheckConfig:
    negative_data_rejection: NegativeDataRejectionConfig = field(default_factory=NegativeDataRejectionConfig)
    positive_data_acceptance: PositiveDataAcceptanceConfig = field(default_factory=PositiveDataAcceptanceConfig)


@dataclass
class CheckContext:
    """Context for Schemathesis checks.

    Provides access to broader test execution data beyond individual test cases.
    """

    override: CaseOverride | None
    auth: HTTPDigestAuth | RawAuth | None
    headers: CaseInsensitiveDict | None
    config: CheckConfig = field(default_factory=CheckConfig)


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
