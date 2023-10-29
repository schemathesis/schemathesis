from enum import Enum
from typing import List, Union, Iterable


class DataGenerationMethod(str, Enum):
    """Defines what data Schemathesis generates for tests."""

    # Generate data, that fits the API schema
    positive = "positive"
    # Doesn't fit the API schema
    negative = "negative"

    @classmethod
    def default(cls) -> "DataGenerationMethod":
        return cls.positive

    @classmethod
    def all(cls) -> List["DataGenerationMethod"]:
        return list(DataGenerationMethod)

    def as_short_name(self) -> str:
        return {
            DataGenerationMethod.positive: "P",
            DataGenerationMethod.negative: "N",
        }[self]

    @property
    def is_negative(self) -> bool:
        return self == DataGenerationMethod.negative

    @classmethod
    def ensure_list(cls, value: "DataGenerationMethodInput") -> List["DataGenerationMethod"]:
        if isinstance(value, DataGenerationMethod):
            return [value]
        return list(value)


DataGenerationMethodInput = Union[DataGenerationMethod, Iterable[DataGenerationMethod]]

DEFAULT_DATA_GENERATION_METHODS = (DataGenerationMethod.default(),)
