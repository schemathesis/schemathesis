from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve
from schemathesis.core.warnings import SchemathesisWarning

__all__ = ["SchemathesisWarning", "WarningsConfig"]

# Share of accepted positive cases below which an operation is reported as under-exercised.
DEFAULT_LOW_VALID_RATE_THRESHOLD = 0.2
# Warnings left out of the default set; a run displays them only when it names them.
OPT_IN_WARNINGS = frozenset({SchemathesisWarning.LOW_VALID_RATE})
DEFAULT_DISPLAYED_WARNINGS = [warning for warning in SchemathesisWarning if warning not in OPT_IN_WARNINGS]


@dataclass(repr=False, slots=True)
class LowValidRateConfig(DiffBase):
    """Options for the `low_valid_rate` warning."""

    threshold: float
    """Share of accepted positive cases below which the warning fires."""

    def __init__(self, *, threshold: float = DEFAULT_LOW_VALID_RATE_THRESHOLD) -> None:
        self.threshold = threshold

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LowValidRateConfig:
        return cls(threshold=data.get("threshold", DEFAULT_LOW_VALID_RATE_THRESHOLD))


@dataclass(repr=False, slots=True)
class WarningsConfig(DiffBase):
    """Configuration for warning display and failure behavior."""

    display: list[SchemathesisWarning]
    """Which warnings to display in output."""

    fail_on: list[SchemathesisWarning]
    """Which warnings should cause test failure."""

    low_valid_rate: LowValidRateConfig
    """Options for the `low_valid_rate` warning."""

    def __init__(
        self,
        *,
        display: list[SchemathesisWarning] | None = None,
        fail_on: list[SchemathesisWarning] | None = None,
        low_valid_rate: LowValidRateConfig | None = None,
    ) -> None:
        self.display = display if display is not None else list(DEFAULT_DISPLAYED_WARNINGS)
        self.fail_on = fail_on if fail_on is not None else []
        self.low_valid_rate = low_valid_rate or LowValidRateConfig()

    @classmethod
    def from_value(cls, value: bool | list[str] | dict[str, Any] | None) -> WarningsConfig:
        """Parse warnings config from bool, list, dict, or None."""
        if value is None or value is True:
            return cls()
        elif value is False:
            return cls(display=[], fail_on=[])
        elif isinstance(value, list):
            warnings = [SchemathesisWarning.from_str(resolve(w)) for w in value]
            return cls(display=warnings, fail_on=[])
        assert isinstance(value, dict)
        enabled = value.get("enabled", True)
        display_list = value.get("display")
        fail_on = value.get("fail-on", False)
        low_valid_rate = LowValidRateConfig.from_dict(value.get("low_valid_rate") or {})

        # Determine which warnings to display
        if not enabled:
            display = []
        elif display_list is not None:
            display = [SchemathesisWarning.from_str(resolve(w)) for w in display_list]
        else:
            display = list(DEFAULT_DISPLAYED_WARNINGS)

        # Determine which warnings should fail
        if fail_on is False or fail_on is None:
            fail_on_list = []
        elif fail_on is True:
            fail_on_list = display.copy()
        elif isinstance(fail_on, list):
            fail_on_list = [SchemathesisWarning.from_str(resolve(w)) for w in fail_on]
        else:
            fail_on_list = []

        return cls(display=display, fail_on=fail_on_list, low_valid_rate=low_valid_rate)

    def should_display(self, warning: SchemathesisWarning) -> bool:
        """Check if a warning should be displayed."""
        return warning in self.display

    def should_fail(self, warning: SchemathesisWarning) -> bool:
        """Check if a warning should cause test failure."""
        return warning in self.fail_on
