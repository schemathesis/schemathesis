from __future__ import annotations

import hashlib
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


def derive_operation_seed(seed: int | None, label: str) -> int | None:
    """Mix a seed with an operation label, so operations with identical parameters draw different inputs."""
    if seed is None:
        return None
    # A stable hash keeps the seed identical across processes, unlike the salted built-in `hash`.
    digest = hashlib.blake2b(f"{seed}:{label}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big")
