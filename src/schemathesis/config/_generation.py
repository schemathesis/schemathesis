from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from schemathesis.config._diff_base import DiffBase
from schemathesis.generation.modes import GenerationMode

if TYPE_CHECKING:
    from schemathesis.generation.targets import TargetFunction


@dataclass(repr=False)
class GenerationConfig(DiffBase):
    modes: list[GenerationMode]
    max_examples: int | None
    seed: int | None
    no_shrink: bool
    deterministic: bool
    allow_x00: bool
    codec: str | None
    maximize: list[TargetFunction]
    with_security_parameters: bool
    graphql_allow_null: bool
    database: str | None
    unique_inputs: bool | None
    fill_missing_examples: bool | None

    __slots__ = (
        "modes",
        "max_examples",
        "seed",
        "no_shrink",
        "deterministic",
        "allow_x00",
        "codec",
        "maximize",
        "with_security_parameters",
        "graphql_allow_null",
        "database",
        "unique_inputs",
        "fill_missing_examples",
    )

    def __init__(
        self,
        *,
        modes: list[GenerationMode] | None = None,
        max_examples: int | None = None,
        seed: int | None = None,
        no_shrink: bool = False,
        deterministic: bool = False,
        allow_x00: bool = True,
        codec: str | None = None,
        maximize: list[TargetFunction] | None = None,
        with_security_parameters: bool = True,
        graphql_allow_null: bool = True,
        database: str | None = None,
        unique_inputs: bool = False,
        fill_missing_examples: bool = False,
    ) -> None:
        from schemathesis.generation import GenerationMode

        # TODO: Switch to `all` by default.
        self.modes = modes or [GenerationMode.POSITIVE]
        self.max_examples = max_examples
        self.seed = seed
        self.no_shrink = no_shrink
        self.deterministic = deterministic
        self.allow_x00 = allow_x00
        self.codec = codec
        self.maximize = maximize or []
        self.with_security_parameters = with_security_parameters
        self.graphql_allow_null = graphql_allow_null
        self.database = database
        self.unique_inputs = unique_inputs
        self.fill_missing_examples = fill_missing_examples

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GenerationConfig:
        from schemathesis.generation.targets import TARGETS

        mode_raw = data.get("mode")
        if mode_raw == "all":
            modes = GenerationMode.all()
        elif mode_raw is not None:
            modes = [GenerationMode(mode_raw)]
        else:
            modes = None
        maximize_raw = data.get("maximize")
        if isinstance(maximize_raw, list):
            targets = maximize_raw
        elif isinstance(maximize_raw, str):
            targets = [maximize_raw]
        else:
            targets = []
        maximize = TARGETS.get_by_names(targets)
        return cls(
            modes=modes,
            max_examples=data.get("max-examples"),
            seed=data.get("seed"),
            no_shrink=data.get("no-shrink", False),
            deterministic=data.get("deterministic", False),
            allow_x00=data.get("allow-x00", True),
            codec=data.get("codec"),
            maximize=maximize,
            with_security_parameters=data.get("with-security-parameters", True),
            graphql_allow_null=data.get("graphql-allow-null", True),
            database=data.get("database"),
            unique_inputs=data.get("unique-inputs", False),
            fill_missing_examples=data.get("fill-missing-examples", False),
        )
