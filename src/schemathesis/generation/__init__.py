from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ._hypothesis import add_single_example, combine_strategies, get_single_example  # noqa: E402
from ._methods import DataGenerationMethod, DataGenerationMethodInput  # noqa: E402

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy


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


@dataclass
class HeaderConfig:
    """Configuration for generating headers."""

    strategy: SearchStrategy[str] | None = None


@dataclass
class GenerationConfig:
    """Holds various configuration options relevant for data generation."""

    # Allow generating `\x00` bytes in strings
    allow_x00: bool = True
    # Allowing using `null` for optional arguments in GraphQL queries
    graphql_allow_null: bool = True
    # Generate strings using the given codec
    codec: str | None = "utf-8"
    # Whether to generate security parameters
    with_security_parameters: bool = True
    # Header generation configuration
    headers: HeaderConfig = field(default_factory=HeaderConfig)
