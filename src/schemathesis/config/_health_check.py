from __future__ import annotations

from enum import Enum, unique
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import hypothesis


@unique
class HealthCheck(str, Enum):
    data_too_large = "data_too_large"
    filter_too_much = "filter_too_much"
    too_slow = "too_slow"
    large_base_example = "large_base_example"
    all = "all"

    def as_hypothesis(self) -> list[hypothesis.HealthCheck]:
        from hypothesis import HealthCheck

        if self.name == "all":
            return list(HealthCheck)

        return [HealthCheck[self.name]]
