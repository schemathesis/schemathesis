"""Implementation of various configuration options required for our research paper.

This code does not target end users and will not be merged. The implementation is designed in the way that
the original Schemathesis code requires minimal changes.
"""
# pylint: disable=import-outside-toplevel
import enum
import os
from typing import Any, List
from unittest.mock import patch


def apply() -> None:
    """Applies features based on env variables."""
    for feature in Feature.all():
        if feature.is_enabled():
            feature.apply()


class Feature(enum.Enum):
    # Disable Open API "format" strategies
    DISABLE_FORMAT_STRATEGIES = enum.auto()
    # Disable Swarm Testing inside negative testing
    DISABLE_SWARM_TESTING = enum.auto()
    # Uses older `hypothesis-jsonschema` version that applies much less pre-processing
    USE_LESS_SCHEMA_PRE_PROCESSING = enum.auto()

    @classmethod
    def all(cls) -> List["Feature"]:
        return list(cls)

    @property
    def env_var(self) -> str:
        return f"SCHEMATHESIS_{self.name}"

    def is_enabled(self) -> bool:
        value = os.environ.get(self.env_var)
        # Any env var value except `0` enables this feature
        return value not in (None, "0")

    def apply(self) -> None:
        if self == Feature.DISABLE_FORMAT_STRATEGIES:
            disable_format_strategies()
        elif self == Feature.DISABLE_SWARM_TESTING:
            disable_swarm_testing()
        elif self == Feature.USE_LESS_SCHEMA_PRE_PROCESSING:
            use_less_schema_pre_processing()


def disable_format_strategies() -> None:
    from .specs.openapi._hypothesis import STRING_FORMATS

    # TODO: Consider replacing `STRING_FORMATS` with a custom class that returns only internal strategies
    #       This way it will prevent user-registered strategies from being used.

    for name in list(STRING_FORMATS):
        # Keep internal formats (header generation, etc)
        if not name.startswith("_"):
            del STRING_FORMATS[name]


def disable_swarm_testing() -> None:
    from hypothesis.strategies._internal import featureflags

    class FeatureFlags:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def is_enabled(self, name: str) -> bool:
            return True

    class FeatureStrategy(featureflags.FeatureStrategy):
        def do_draw(self, data: Any) -> FeatureFlags:
            return FeatureFlags(data)

    # Every feature is always enabled that effectively disables Swarm Testing
    patched = patch("schemathesis.specs.openapi.negative.mutations.FeatureStrategy", FeatureStrategy)
    patched.start()


def use_less_schema_pre_processing() -> None:
    # TODO: Patches the installed `hypothesis-jsonschema` version with a bundled one
    pass
