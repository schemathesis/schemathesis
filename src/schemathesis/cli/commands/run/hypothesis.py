from __future__ import annotations

from enum import IntEnum, unique
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    import hypothesis

PHASES_INVALID_USAGE_MESSAGE = "Can't use `--hypothesis-phases` and `--hypothesis-no-phases` simultaneously"
HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER = ":memory:"

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


def prepare_health_checks(
    hypothesis_suppress_health_check: list[HealthCheck] | None,
) -> list[hypothesis.HealthCheck] | None:
    if hypothesis_suppress_health_check is None:
        return None

    return [entry for health_check in hypothesis_suppress_health_check for entry in health_check.as_hypothesis()]


def prepare_phases(
    hypothesis_phases: list[Phase] | None, hypothesis_no_phases: list[Phase] | None
) -> list[hypothesis.Phase] | None:
    if hypothesis_phases is not None and hypothesis_no_phases is not None:
        raise click.UsageError(PHASES_INVALID_USAGE_MESSAGE)
    if hypothesis_phases:
        return [phase.as_hypothesis() for phase in hypothesis_phases]
    if hypothesis_no_phases:
        return Phase.filter_from_all(hypothesis_no_phases)
    return None


def prepare_settings(
    database: str | None = None,
    derandomize: bool | None = None,
    max_examples: int | None = None,
    phases: list[hypothesis.Phase] | None = None,
    suppress_health_check: list[hypothesis.HealthCheck] | None = None,
) -> hypothesis.settings:
    import hypothesis
    from hypothesis.database import DirectoryBasedExampleDatabase, InMemoryExampleDatabase

    kwargs: dict[str, Any] = {
        key: value
        for key, value in (
            ("derandomize", derandomize),
            ("max_examples", max_examples),
            ("phases", phases),
            ("suppress_health_check", suppress_health_check),
        )
        if value is not None
    }
    if database is not None:
        if database.lower() == "none":
            kwargs["database"] = None
        elif database == HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER:
            kwargs["database"] = InMemoryExampleDatabase()
        else:
            kwargs["database"] = DirectoryBasedExampleDatabase(database)
    return hypothesis.settings(print_blob=False, deadline=None, verbosity=hypothesis.Verbosity.quiet, **kwargs)
