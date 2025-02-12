from __future__ import annotations

from enum import Enum, unique
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import hypothesis

HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER = ":memory:"

# Importing Hypothesis is expensive, hence we re-create the enums we need in CLI commands definitions
# Hypothesis is stable, hence it should not be a problem and adding new variants should not be automatic


@unique
class HealthCheck(str, Enum):
    # We remove not relevant checks
    data_too_large = "data_too_large"
    filter_too_much = "filter_too_much"
    too_slow = "too_slow"
    large_base_example = "large_base_example"
    all = "all"

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


def prepare_phases(no_shrink: bool = False) -> list[hypothesis.Phase] | None:
    from hypothesis import Phase

    phases = set(Phase) - {Phase.explain}
    if no_shrink:
        return list(phases - {Phase.shrink})
    return list(phases)


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
