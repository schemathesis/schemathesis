from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Literal, TypedDict, Union


class UploadSource(str, Enum):
    DEFAULT = "default"
    UPLOAD_COMMAND = "upload_command"


@dataclass
class ProjectDetails:
    environments: list[ProjectEnvironment]
    specification: Specification

    @property
    def default_environment(self) -> ProjectEnvironment | None:
        return next((env for env in self.environments if env.is_default), None)


@dataclass
class ProjectEnvironment:
    url: str
    name: str
    description: str
    is_default: bool


@dataclass
class Specification:
    schema: dict[str, Any]


@dataclass
class AuthResponse:
    username: str


@dataclass
class UploadResponse:
    message: str
    next_url: str
    correlation_id: str


@dataclass
class FailedUploadResponse:
    detail: str


@dataclass
class NotAppliedState:
    """The extension was not applied."""

    def __str__(self) -> str:
        return "Not Applied"


@dataclass
class SuccessState:
    """The extension was applied successfully."""

    def __str__(self) -> str:
        return "Success"


@dataclass
class ErrorState:
    """An error occurred during the extension application."""

    errors: list[str] = field(default_factory=list)
    exceptions: list[Exception] = field(default_factory=list)

    def __str__(self) -> str:
        return "Error"


ExtensionState = Union[NotAppliedState, SuccessState, ErrorState]


@dataclass
class BaseExtension:
    def set_state(self, state: ExtensionState) -> None:
        self.state = state

    def set_success(self) -> None:
        self.set_state(SuccessState())

    def set_error(self, errors: list[str] | None = None, exceptions: list[Exception] | None = None) -> None:
        self.set_state(ErrorState(errors=errors or [], exceptions=exceptions or []))


@dataclass
class UnknownExtension(BaseExtension):
    """An unknown extension.

    Likely the CLI should be updated.
    """

    type: str
    state: ExtensionState = field(default_factory=NotAppliedState)

    @property
    def summary(self) -> str:
        return f"`{self.type}`"


class AddPatch(TypedDict):
    operation: Literal["add"]
    path: list[str | int]
    value: Any


class RemovePatch(TypedDict):
    operation: Literal["remove"]
    path: list[str | int]


Patch = Union[AddPatch, RemovePatch]


@dataclass
class SchemaPatchesExtension(BaseExtension):
    """Update the schema with its optimized version."""

    patches: list[Patch]
    state: ExtensionState = field(default_factory=NotAppliedState)

    @property
    def summary(self) -> str:
        count = len(self.patches)
        plural = "es" if count > 1 else ""
        return f"{count} schema patch{plural}"


class TransformFunctionDefinition(TypedDict):
    kind: Literal["map", "filter"]
    name: str
    arguments: dict[str, Any]


@dataclass
class StrategyDefinition:
    name: str
    transforms: list[TransformFunctionDefinition] | None = None
    arguments: dict[str, Any] | None = None


def _strategies_from_definition(items: dict[str, list[dict[str, Any]]]) -> dict[str, list[StrategyDefinition]]:
    return {name: [StrategyDefinition(**item) for item in value] for name, value in items.items()}


def _format_items(items: Iterable[str]) -> str:
    return ", ".join([f"`{item}`" for item in items])


@dataclass
class OpenApiStringFormatsExtension(BaseExtension):
    """Custom string formats."""

    formats: dict[str, list[StrategyDefinition]]
    state: ExtensionState = field(default_factory=NotAppliedState)

    @classmethod
    def from_dict(cls, formats: dict[str, list[dict[str, Any]]]) -> OpenApiStringFormatsExtension:
        return cls(formats=_strategies_from_definition(formats))

    @property
    def summary(self) -> str:
        count = len(self.formats)
        plural = "s" if count > 1 else ""
        formats = _format_items(self.formats)
        return f"Data generator{plural} for {formats} Open API format{plural}"


@dataclass
class GraphQLScalarsExtension(BaseExtension):
    """Custom scalars."""

    scalars: dict[str, list[StrategyDefinition]]
    state: ExtensionState = field(default_factory=NotAppliedState)

    @classmethod
    def from_dict(cls, scalars: dict[str, list[dict[str, Any]]]) -> GraphQLScalarsExtension:
        return cls(scalars=_strategies_from_definition(scalars))

    @property
    def summary(self) -> str:
        count = len(self.scalars)
        plural = "s" if count > 1 else ""
        scalars = _format_items(self.scalars)
        return f"Data generator{plural} for {scalars} GraphQL scalar{plural}"


@dataclass
class MediaTypesExtension(BaseExtension):
    media_types: dict[str, list[StrategyDefinition]]
    state: ExtensionState = field(default_factory=NotAppliedState)

    @classmethod
    def from_dict(cls, media_types: dict[str, list[dict[str, Any]]]) -> MediaTypesExtension:
        return cls(media_types=_strategies_from_definition(media_types))

    @property
    def summary(self) -> str:
        count = len(self.media_types)
        plural = "s" if count > 1 else ""
        media_types = _format_items(self.media_types)
        return f"Data generator{plural} for {media_types} media type{plural}"


# A CLI extension that can be used to adjust the behavior of Schemathesis.
Extension = Union[
    SchemaPatchesExtension,
    OpenApiStringFormatsExtension,
    GraphQLScalarsExtension,
    MediaTypesExtension,
    UnknownExtension,
]


def extension_from_dict(data: dict[str, Any]) -> Extension:
    if data["type"] == "schema_patches":
        return SchemaPatchesExtension(patches=data["patches"])
    elif data["type"] == "string_formats":
        return OpenApiStringFormatsExtension.from_dict(formats=data["items"])
    elif data["type"] == "scalars":
        return GraphQLScalarsExtension.from_dict(scalars=data["items"])
    elif data["type"] == "media_types":
        return MediaTypesExtension.from_dict(media_types=data["items"])
    return UnknownExtension(type=data["type"])


@dataclass
class AnalysisSuccess:
    id: str
    elapsed: float
    message: str
    extensions: list[Extension]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalysisSuccess:
        return cls(
            id=data["id"],
            elapsed=data["elapsed"],
            message=data["message"],
            extensions=[extension_from_dict(ext) for ext in data["extensions"]],
        )


@dataclass
class AnalysisError:
    message: str


AnalysisResult = Union[AnalysisSuccess, AnalysisError]
