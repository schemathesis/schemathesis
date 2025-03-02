from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemathesis.config._diff_base import DiffBase
from schemathesis.config._generation import GenerationConfig
from schemathesis.config._operations import OperationConfig
from schemathesis.config._parameters import ParameterOverride, load_parameters


@dataclass(repr=False)
class ProjectConfig(DiffBase):
    base_url: str | None
    parameters: dict[str, ParameterOverride]
    generation: GenerationConfig
    operations: list[OperationConfig]

    __slots__ = (
        "base_url",
        "parameters",
        "generation",
        "operations",
    )

    def __init__(
        self,
        *,
        base_url: str | None = None,
        parameters: dict[str, ParameterOverride] | None = None,
        generation: GenerationConfig | None = None,
        operations: list[OperationConfig] | None = None,
    ) -> None:
        self.base_url = base_url
        self.parameters = parameters or {}
        self.generation = generation or GenerationConfig()
        self.operations = operations or []

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectConfig:
        return cls(
            base_url=data.get("base-url"),
            parameters=load_parameters(data),
            generation=GenerationConfig.from_dict(data.get("generation", {})),
            operations=[OperationConfig.from_dict(operation) for operation in data.get("operations", [])],
        )

    def override(self, *, base_url: str | None) -> None:
        if base_url is not None:
            self.base_url = base_url


@dataclass(repr=False)
class ProjectsConfig(DiffBase):
    default: ProjectConfig
    named: dict[str, ProjectConfig]

    __slots__ = ("default", "named")

    def __init__(
        self,
        *,
        default: ProjectConfig | None = None,
        named: dict[str, ProjectConfig] | None = None,
    ) -> None:
        self.default = default or ProjectConfig()
        self.named = named or {}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectsConfig:
        return cls(default=ProjectConfig.from_dict(data), named={})
