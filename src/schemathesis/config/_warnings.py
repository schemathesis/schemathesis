from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve


class SchemathesisWarning(str, enum.Enum):
    MISSING_AUTH = "missing_auth"
    MISSING_TEST_DATA = "missing_test_data"
    VALIDATION_MISMATCH = "validation_mismatch"
    MISSING_DESERIALIZER = "missing_deserializer"
    UNUSED_OPENAPI_AUTH = "unused_openapi_auth"
    UNSUPPORTED_REGEX = "unsupported_regex"

    @classmethod
    def from_str(cls, value: str) -> SchemathesisWarning:
        return {
            "missing_auth": cls.MISSING_AUTH,
            "missing_test_data": cls.MISSING_TEST_DATA,
            "validation_mismatch": cls.VALIDATION_MISMATCH,
            "missing_deserializer": cls.MISSING_DESERIALIZER,
            "unused_openapi_auth": cls.UNUSED_OPENAPI_AUTH,
            "unsupported_regex": cls.UNSUPPORTED_REGEX,
        }[value.lower()]


@dataclass(repr=False)
class WarningsConfig(DiffBase):
    """Configuration for warning display and failure behavior."""

    display: list[SchemathesisWarning]
    """Which warnings to display in output."""

    fail_on: list[SchemathesisWarning]
    """Which warnings should cause test failure."""

    __slots__ = ("display", "fail_on")

    def __init__(
        self,
        *,
        display: list[SchemathesisWarning] | None = None,
        fail_on: list[SchemathesisWarning] | None = None,
    ) -> None:
        self.display = display if display is not None else list(SchemathesisWarning)
        self.fail_on = fail_on if fail_on is not None else []

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

        # Determine which warnings to display
        if not enabled:
            display = []
        elif display_list is not None:
            display = [SchemathesisWarning.from_str(resolve(w)) for w in display_list]
        else:
            display = list(SchemathesisWarning)

        # Determine which warnings should fail
        if fail_on is False or fail_on is None:
            fail_on_list = []
        elif fail_on is True:
            fail_on_list = display.copy()
        elif isinstance(fail_on, list):
            fail_on_list = [SchemathesisWarning.from_str(resolve(w)) for w in fail_on]
        else:
            fail_on_list = []

        return cls(display=display, fail_on=fail_on_list)

    def should_display(self, warning: SchemathesisWarning) -> bool:
        """Check if a warning should be displayed."""
        return warning in self.display

    def should_fail(self, warning: SchemathesisWarning) -> bool:
        """Check if a warning should cause test failure."""
        return warning in self.fail_on
