from __future__ import annotations

from typing import TYPE_CHECKING, Any

from schemathesis.config._health_check import HealthCheck

if TYPE_CHECKING:
    import hypothesis

HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER = ":memory:"


def prepare_phases(no_shrink: bool = False) -> list[hypothesis.Phase] | None:
    from hypothesis import Phase

    phases = set(Phase) - {Phase.explain}
    if no_shrink:
        return list(phases - {Phase.shrink})
    return list(phases)


def prepare_settings(
    *,
    database: str | None = None,
    derandomize: bool | None = None,
    max_examples: int | None = None,
    phases: list[hypothesis.Phase] | None = None,
    suppress_health_check: list[HealthCheck],
) -> hypothesis.settings:
    import hypothesis
    from hypothesis.database import DirectoryBasedExampleDatabase, InMemoryExampleDatabase

    kwargs: dict[str, Any] = {
        key: value
        for key, value in (
            ("derandomize", derandomize),
            ("max_examples", max_examples),
            ("phases", phases),
            (
                "suppress_health_check",
                [check for item in suppress_health_check for check in item.as_hypothesis()],
            ),
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
