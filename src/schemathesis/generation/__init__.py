from __future__ import annotations

import random

from schemathesis.generation.modes import GenerationMode

__all__ = [
    "GenerationMode",
    "generate_random_case_id",
]


CASE_ID_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
# Separate `Random` as Hypothesis might interfere with the default one
RANDOM = random.Random()


def generate_random_case_id(length: int = 6) -> str:
    return "".join(RANDOM.choices(CASE_ID_ALPHABET, k=length))
