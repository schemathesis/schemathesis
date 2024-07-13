from __future__ import annotations

from enum import IntEnum, unique
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import hypothesis

MIN_WORKERS = 1
DEFAULT_WORKERS = MIN_WORKERS
MAX_WORKERS = 64

# Importing Hypothesis is expensive, hence we re-create the enums we need in CLI commands definitions
# Hypothesis is stable, hence it should not be a problem and adding new variants should not be automatic


@unique
class Phase(IntEnum):
    explicit = 0  #: controls whether explicit examples are run.
    reuse = 1  #: controls whether previous examples will be reused.
    generate = 2  #: controls whether new examples will be generated.
    target = 3  #: controls whether examples will be mutated for targeting.
    shrink = 4  #: controls whether examples will be shrunk.
    # The `explain` phase is not supported

    def as_hypothesis(self) -> hypothesis.Phase:
        from hypothesis import Phase

        return Phase[self.name]

    @staticmethod
    def filter_from_all(variants: list[Phase]) -> list[hypothesis.Phase]:
        from hypothesis import Phase

        return list(set(Phase) - {Phase.explain} - set(variants))


@unique
class HealthCheck(IntEnum):
    # We remove not relevant checks
    data_too_large = 1
    filter_too_much = 2
    too_slow = 3
    large_base_example = 7
    all = 8

    def as_hypothesis(self) -> list[hypothesis.HealthCheck]:
        from hypothesis import HealthCheck

        if self.name == "all":
            return list(HealthCheck)

        return [HealthCheck[self.name]]


@unique
class Verbosity(IntEnum):
    quiet = 0
    normal = 1
    verbose = 2
    debug = 3
