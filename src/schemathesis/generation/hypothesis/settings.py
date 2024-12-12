from __future__ import annotations

from typing import TYPE_CHECKING, Any

from schemathesis.core import NotSet
from schemathesis.generation.hypothesis import DEFAULT_DEADLINE, HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER

if TYPE_CHECKING:
    import hypothesis


def prepare(
    database: str | None = None,
    deadline: int | NotSet | None = None,
    derandomize: bool | None = None,
    max_examples: int | None = None,
    phases: list[hypothesis.Phase] | None = None,
    report_multiple_bugs: bool | None = None,
    suppress_health_check: list[hypothesis.HealthCheck] | None = None,
    verbosity: hypothesis.Verbosity | None = None,
) -> hypothesis.settings:
    import hypothesis
    from hypothesis.database import DirectoryBasedExampleDatabase, InMemoryExampleDatabase

    kwargs: dict[str, Any] = {
        key: value
        for key, value in (
            ("derandomize", derandomize),
            ("max_examples", max_examples),
            ("phases", phases),
            ("report_multiple_bugs", report_multiple_bugs),
            ("suppress_health_check", suppress_health_check),
            ("verbosity", verbosity),
        )
        if value is not None
    }
    # `deadline` is special, since Hypothesis allows passing `None`
    if deadline is not None:
        if isinstance(deadline, NotSet):
            kwargs["deadline"] = None
        else:
            kwargs["deadline"] = deadline
    if database is not None:
        if database.lower() == "none":
            kwargs["database"] = None
        elif database == HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER:
            kwargs["database"] = InMemoryExampleDatabase()
        else:
            kwargs["database"] = DirectoryBasedExampleDatabase(database)
    kwargs.setdefault("deadline", DEFAULT_DEADLINE)
    return hypothesis.settings(print_blob=False, **kwargs)
