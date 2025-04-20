from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from schemathesis.config import ProjectConfig, SchemathesisConfig

if TYPE_CHECKING:
    import hypothesis

    from schemathesis.engine.core import Engine
    from schemathesis.schemas import BaseSchema


class Status(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    INTERRUPTED = "interrupted"
    SKIP = "skip"

    def __lt__(self, other: Status) -> bool:  # type: ignore[override]
        return _STATUS_ORDER[self] < _STATUS_ORDER[other]


_STATUS_ORDER = {Status.SUCCESS: 0, Status.FAILURE: 1, Status.ERROR: 2, Status.INTERRUPTED: 3, Status.SKIP: 4}


@dataclass
class EngineConfig:
    """Configuration for Schemathesis engine."""

    run: SchemathesisConfig
    project: ProjectConfig

    __slots__ = ("run", "project")

    @classmethod
    def discover(cls) -> EngineConfig:
        run = SchemathesisConfig.discover()
        project = run.projects.default
        return cls(run=run, project=project)

    def get_hypothesis_settings(self) -> hypothesis.settings:
        # TODO: rework so it accepts optional operation / phase too
        import hypothesis

        # "database",
        # "phases",
        # "stateful_step_count",
        # "suppress_health_check",
        # "deadline",
        kwargs: dict[str, Any] = {
            "derandomize": self.project.generation.deterministic,
            "deadline": None,
        }
        if self.project.generation.max_examples is not None:
            kwargs["max_examples"] = self.project.generation.max_examples
        # TODO: prepare
        # suppress_health_check = self.run.suppress_health_check
        # TODO: Prepare DB settings
        # database = self.project.generation.database
        # Prepare phases

        return hypothesis.settings(**kwargs)


def from_schema(schema: BaseSchema, *, config: EngineConfig | None = None) -> Engine:
    from .core import Engine

    if config is None:
        config = EngineConfig.discover()

    return Engine(schema=schema, config=config)
