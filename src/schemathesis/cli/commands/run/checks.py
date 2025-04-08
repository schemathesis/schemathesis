from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from schemathesis.checks import CHECKS, CheckFunction, ChecksConfig


@dataclass
class CheckArguments:
    included_check_names: Sequence[str]
    excluded_check_names: Sequence[str]
    max_response_time: float | None

    __slots__ = (
        "included_check_names",
        "excluded_check_names",
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

        from schemathesis.specs.openapi.checks import positive_data_acceptance

        selected_checks.append(positive_data_acceptance)

        from schemathesis.specs.openapi.checks import missing_required_header

        selected_checks.append(missing_required_header)

        if self.max_response_time is not None:
            from schemathesis.checks import max_response_time as _max_response_time

            selected_checks.append(_max_response_time)

        from schemathesis.specs.openapi.checks import unsupported_method

        selected_checks.append(unsupported_method)

        # Exclude checks based on their names
        selected_checks = [check for check in selected_checks if check.__name__ not in self.excluded_check_names]

        return selected_checks, checks_config
