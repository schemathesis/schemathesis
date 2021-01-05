from enum import Enum

from ._compat import metadata

try:
    __version__ = metadata.version(__package__)
except metadata.PackageNotFoundError:
    # Local run without installation
    __version__ = "dev"


USER_AGENT = f"schemathesis/{__version__}"
DEFAULT_DEADLINE = 500  # pragma: no mutate
DEFAULT_STATEFUL_RECURSION_LIMIT = 5  # pragma: no mutate
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

    @classmethod
    def default(cls) -> "DataGenerationMethod":
        return cls.positive

    def as_short_name(self) -> str:
        return {
            DataGenerationMethod.positive: "P",
        }[self]


DEFAULT_DATA_GENERATION_METHODS = (DataGenerationMethod.default(),)
