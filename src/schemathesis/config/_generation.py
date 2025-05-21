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
    no_shrink: bool
    deterministic: bool
    # Allow generating `\x00` bytes in strings
    allow_x00: bool
    # Generate strings using the given codec
    codec: str | None
    maximize: list[TargetFunction]
    # Whether to generate security parameters
    with_security_parameters: bool
    # Allowing using `null` for optional arguments in GraphQL queries
    graphql_allow_null: bool
    database: str | None
    unique_inputs: bool
    exclude_header_characters: str | None

    __slots__ = (
        "modes",
        "max_examples",
        "no_shrink",
        "deterministic",
        "allow_x00",
        "codec",
        "maximize",
        "with_security_parameters",
        "graphql_allow_null",
        "database",
        "unique_inputs",
        "exclude_header_characters",
    )

    def __init__(
        self,
        *,
        modes: list[GenerationMode] | None = None,
        max_examples: int | None = None,
        no_shrink: bool = False,
        deterministic: bool = False,
        allow_x00: bool = True,
        codec: str | None = "utf-8",
        maximize: list[TargetFunction] | None = None,
        with_security_parameters: bool = True,
        graphql_allow_null: bool = True,
        database: str | None = None,
        unique_inputs: bool = False,
        exclude_header_characters: str | None = None,
    ) -> None:
        from schemathesis.generation import GenerationMode

        # TODO: Switch to `all` by default.
        self.modes = modes or [GenerationMode.POSITIVE]
        self.max_examples = max_examples
        self.no_shrink = no_shrink
        self.deterministic = deterministic
        self.allow_x00 = allow_x00
        self.codec = codec
        self.maximize = maximize or []
        self.with_security_parameters = with_security_parameters
        self.graphql_allow_null = graphql_allow_null
        self.database = database
        self.unique_inputs = unique_inputs
        self.exclude_header_characters = exclude_header_characters

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GenerationConfig:
        mode_raw = data.get("mode")
        if mode_raw == "all":
            modes = GenerationMode.all()
        elif mode_raw is not None:
            modes = [GenerationMode(mode_raw)]
        else:
            modes = None
        maximize = _get_maximize(data.get("maximize"))
        return cls(
            modes=modes,
            max_examples=data.get("max-examples"),
            no_shrink=data.get("no-shrink", False),
            deterministic=data.get("deterministic", False),
            allow_x00=data.get("allow-x00", True),
            codec=data.get("codec", "utf-8"),
            maximize=maximize,
            with_security_parameters=data.get("with-security-parameters", True),
            graphql_allow_null=data.get("graphql-allow-null", True),
            database=data.get("database"),
            unique_inputs=data.get("unique-inputs", False),
            exclude_header_characters=data.get("exclude-header-characters"),
        )

    def update(
        self,
        *,
        modes: list[GenerationMode] | None = None,
        max_examples: int | None = None,
        no_shrink: bool = False,
        deterministic: bool | None = None,
        allow_x00: bool = True,
        codec: str | None = None,
        maximize: list[TargetFunction] | None = None,
        with_security_parameters: bool | None = None,
        graphql_allow_null: bool = True,
        database: str | None = None,
        unique_inputs: bool = False,
        exclude_header_characters: str | None = None,
    ) -> None:
        if modes is not None:
            self.modes = modes
        if max_examples is not None:
            self.max_examples = max_examples
        self.no_shrink = no_shrink
        self.deterministic = deterministic or False
        self.allow_x00 = allow_x00
        if codec is not None:
            self.codec = codec
        if maximize is not None:
            self.maximize = maximize
        if with_security_parameters is not None:
            self.with_security_parameters = with_security_parameters
        self.graphql_allow_null = graphql_allow_null
        if database is not None:
            self.database = database
        self.unique_inputs = unique_inputs
        if exclude_header_characters is not None:
            self.exclude_header_characters = exclude_header_characters


def _get_maximize(value: Any) -> list[TargetFunction]:
    from schemathesis.generation.targets import TARGETS

    if isinstance(value, list):
        targets = value
    elif isinstance(value, str):
        targets = [value]
    else:
        targets = []
    return TARGETS.get_by_names(targets)
