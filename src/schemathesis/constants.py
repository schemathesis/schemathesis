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
IS_PYTEST_ABOVE_7 = version.parse(pytest.__version__) >= version.parse("7.0.0")

USER_AGENT = f"schemathesis/{__version__}"
SCHEMATHESIS_TEST_CASE_HEADER = "X-Schemathesis-TestCaseId"
HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER = ":memory:"
DISCORD_LINK = "https://discord.gg/R9ASRAmHnA"
# Maximum test running time
DEFAULT_DEADLINE = 15000
DEFAULT_RESPONSE_TIMEOUT = 10000
DEFAULT_STATEFUL_RECURSION_LIMIT = 5
HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
RECURSIVE_REFERENCE_ERROR_MESSAGE = (
    "Currently, Schemathesis can't generate data for this operation due to "
    "recursive references in the operation definition. See more information in "
    "this issue - https://github.com/schemathesis/schemathesis/issues/947"
)
SERIALIZERS_SUGGESTION_MESSAGE = (
    "You can register your own serializer with `schemathesis.serializer` "
    "and Schemathesis will be able to make API calls with this media type. \n"
    "See https://schemathesis.readthedocs.io/en/stable/how.html#payload-serialization for more information."
)
FLAKY_FAILURE_MESSAGE = "[FLAKY] Schemathesis was not able to reliably reproduce this failure"
BOM_MARK = "\ufeff"
WAIT_FOR_SCHEMA_INTERVAL = 0.05
HOOKS_MODULE_ENV_VAR = "SCHEMATHESIS_HOOKS"
API_NAME_ENV_VAR = "SCHEMATHESIS_API_NAME"
BASE_URL_ENV_VAR = "SCHEMATHESIS_BASE_URL"
WAIT_FOR_SCHEMA_ENV_VAR = "SCHEMATHESIS_WAIT_FOR_SCHEMA"
REPORT_SUGGESTION_ENV_VAR = "SCHEMATHESIS_REPORT_SUGGESTION"


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


DEFAULT_DATA_GENERATION_METHODS = (DataGenerationMethod.default(),)
