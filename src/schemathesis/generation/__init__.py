from __future__ import annotations

import hashlib
import random

from schemathesis.generation.modes import GenerationMode

__all__ = [
    "GenerationMode",
    "generate_random_case_id",
]


CASE_ID_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
# A separate `Random`, since Hypothesis reseeds the global one inside tests
RANDOM = random.Random()


def generate_random_case_id(length: int = 6) -> str:
    return "".join(RANDOM.choices(CASE_ID_ALPHABET, k=length))


def derive_operation_seed(seed: int | None, label: str) -> int | None:
    """Mix a seed with an operation label, so operations with identical parameters draw different inputs."""
    return None if seed is None else _mix(seed, label)


def derive_repeat_seed(seed: int | None, index: int) -> int | None:
    """Seed for the `index`-th repeat of a run; the first one keeps the run seed.

    Mixed with the seed, so a run seeded `N + 1` does not replay the repeats of a run seeded `N`.
    """
    if seed is None or index == 0:
        return seed
    return _mix(seed, f"repeat-{index}")


def _mix(seed: int, salt: str) -> int:
    # A stable hash keeps the seed identical across processes, unlike the salted built-in `hash`.
    digest = hashlib.blake2b(f"{seed}:{salt}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big")
