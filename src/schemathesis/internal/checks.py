from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from requests.structures import CaseInsensitiveDict

    from .._override import CaseOverride
    from ..models import Case
    from ..transports.responses import GenericResponse


CheckFunction = Callable[["CheckContext", "GenericResponse", "Case"], Optional[bool]]


@dataclass
class NegativeDataRejectionConfig:
    # 5xx will pass through
    allowed_statuses: list[str] = field(default_factory=lambda: ["400", "401", "403", "404", "422", "5xx"])


@dataclass
class PositiveDataAcceptanceConfig:
    allowed_statuses: list[str] = field(default_factory=lambda: ["2xx", "401", "403", "404"])


@dataclass
class MissingRequiredHeaderConfig:
    allowed_statuses: list[str] = field(default_factory=lambda: ["406"])


@dataclass
class CheckConfig:
    missing_required_header: MissingRequiredHeaderConfig = field(default_factory=MissingRequiredHeaderConfig)
    negative_data_rejection: NegativeDataRejectionConfig = field(default_factory=NegativeDataRejectionConfig)
    positive_data_acceptance: PositiveDataAcceptanceConfig = field(default_factory=PositiveDataAcceptanceConfig)


@dataclass
class CheckContext:
    """Context for Schemathesis checks.

    Provides access to broader test execution data beyond individual test cases.
    """

    override: CaseOverride | None
    auth: tuple[str, str] | None
    headers: CaseInsensitiveDict | None
    config: CheckConfig = field(default_factory=CheckConfig)
