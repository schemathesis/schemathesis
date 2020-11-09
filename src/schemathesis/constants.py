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
