from __future__ import annotations

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
