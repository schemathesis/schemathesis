from enum import Enum
from typing import List

import pytest
from packaging import version

from ._compat import metadata

try:
    __version__ = metadata.version(__package__)
except metadata.PackageNotFoundError:
    # Local run without installation
    __version__ = "dev"

IS_PYTEST_ABOVE_54 = version.parse(pytest.__version__) >= version.parse("5.4.0")

USER_AGENT = f"schemathesis/{__version__}"
# Maximum test running time
DEFAULT_DEADLINE = 15000  # pragma: no mutate
DEFAULT_RESPONSE_TIMEOUT = 10000  # pragma: no mutate
DEFAULT_STATEFUL_RECURSION_LIMIT = 5  # pragma: no mutate
HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
RECURSIVE_REFERENCE_ERROR_MESSAGE = (
    "Currently, Schemathesis can't generate data for this operation due to "
    "recursive references in the operation definition. See more information in "
    "this issue - https://github.com/schemathesis/schemathesis/issues/947"
)
SERIALIZERS_SUGGESTION_MESSAGE = (
    "You can register your own serializer with `schemathesis.serializers.register` "
    "and Schemathesis will be able to make API calls with this media type. \n"
    "See https://schemathesis.readthedocs.io/en/stable/how.html#payload-serialization for more information."
)


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


DEFAULT_DATA_GENERATION_METHODS = (DataGenerationMethod.default(),)


class CodeSampleStyle(str, Enum):
    """Controls the style of code samples for failure reproduction."""

    python = "python"
    curl = "curl"

    @classmethod
    def default(cls) -> "CodeSampleStyle":
        return cls.curl

    @classmethod
    def from_str(cls, value: str) -> "CodeSampleStyle":
        try:
            return cls[value]
        except KeyError:
            available_styles = ", ".join(cls)
            raise ValueError(
                f"Invalid value for code sample style: {value}. Available styles: {available_styles}"
            ) from None
