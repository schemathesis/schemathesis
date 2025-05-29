from __future__ import annotations

from enum import Enum


class GenerationMode(str, Enum):
    """Defines what data Schemathesis generates for tests."""

    # Generate data, that fits the API schema
    POSITIVE = "positive"
    # Doesn't fit the API schema
    NEGATIVE = "negative"

    @property
    def is_positive(self) -> bool:
        return self == GenerationMode.POSITIVE

    @property
    def is_negative(self) -> bool:
        return self == GenerationMode.NEGATIVE
