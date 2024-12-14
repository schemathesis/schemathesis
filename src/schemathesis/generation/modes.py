from __future__ import annotations

from enum import Enum


class GeneratorMode(str, Enum):
    """Defines what data Schemathesis generates for tests."""

    # Generate data, that fits the API schema
    positive = "positive"
    # Doesn't fit the API schema
    negative = "negative"

    @classmethod
    def default(cls) -> GeneratorMode:
        return cls.positive

    @classmethod
    def all(cls) -> list[GeneratorMode]:
        return list(GeneratorMode)

    def as_short_name(self) -> str:
        return {
            GeneratorMode.positive: "P",
            GeneratorMode.negative: "N",
        }[self]

    @property
    def is_positive(self) -> bool:
        return self == GeneratorMode.positive

    @property
    def is_negative(self) -> bool:
        return self == GeneratorMode.negative
