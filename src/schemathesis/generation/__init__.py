import random
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


CASE_ID_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = len(CASE_ID_ALPHABET)
# Separate `Random` as Hypothesis might interfere with the default one
RANDOM = random.Random()


def generate_random_case_id(length: int = 6) -> str:
    number = RANDOM.randint(62 ** (length - 1), 62**length - 1)
    output = ""
    while number > 0:
        number, rem = divmod(number, BASE)
        output += CASE_ID_ALPHABET[rem]
    return output
