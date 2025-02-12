from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from schemathesis import experimental
from schemathesis.checks import CHECKS, CheckFunction, ChecksConfig


@dataclass
class CheckArguments:
    included_check_names: Sequence[str]
    excluded_check_names: Sequence[str]
    positive_data_acceptance_allowed_statuses: list[str] | None
    missing_required_header_allowed_statuses: list[str] | None
    negative_data_rejection_allowed_statuses: list[str] | None
    max_response_time: float | None

    __slots__ = (
        "included_check_names",
        "excluded_check_names",
        "positive_data_acceptance_allowed_statuses",
        "missing_required_header_allowed_statuses",
        "negative_data_rejection_allowed_statuses",
        "max_response_time",
    )

    def into(self) -> tuple[list[CheckFunction], ChecksConfig]:
        # Determine selected checks
        if "all" in self.included_check_names:
            selected_checks = CHECKS.get_all()
        else:
            selected_checks = CHECKS.get_by_names(self.included_check_names or [])

        # Prepare checks configuration
        checks_config: ChecksConfig = {}

        if experimental.POSITIVE_DATA_ACCEPTANCE.is_enabled:
            from schemathesis.openapi.checks import PositiveDataAcceptanceConfig
            from schemathesis.specs.openapi.checks import positive_data_acceptance

            selected_checks.append(positive_data_acceptance)
            if self.positive_data_acceptance_allowed_statuses:
                checks_config[positive_data_acceptance] = PositiveDataAcceptanceConfig(
                    allowed_statuses=self.positive_data_acceptance_allowed_statuses
                )

        if self.missing_required_header_allowed_statuses:
            from schemathesis.openapi.checks import MissingRequiredHeaderConfig
            from schemathesis.specs.openapi.checks import missing_required_header

            selected_checks.append(missing_required_header)
            checks_config[missing_required_header] = MissingRequiredHeaderConfig(
                allowed_statuses=self.missing_required_header_allowed_statuses
            )

        if self.negative_data_rejection_allowed_statuses:
            from schemathesis.openapi.checks import NegativeDataRejectionConfig
            from schemathesis.specs.openapi.checks import negative_data_rejection

            checks_config[negative_data_rejection] = NegativeDataRejectionConfig(
                allowed_statuses=self.negative_data_rejection_allowed_statuses
            )

        if self.max_response_time is not None:
            from schemathesis.checks import max_response_time as _max_response_time
            from schemathesis.core.failures import MaxResponseTimeConfig

            checks_config[_max_response_time] = MaxResponseTimeConfig(self.max_response_time)
            selected_checks.append(_max_response_time)

        from schemathesis.specs.openapi.checks import unsupported_method

        selected_checks.append(unsupported_method)

        # Exclude checks based on their names
        selected_checks = [check for check in selected_checks if check.__name__ not in self.excluded_check_names]

        return selected_checks, checks_config
