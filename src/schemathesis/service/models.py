from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypedDict, Union, Literal


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
class NotApplied:
    """The extension was not applied."""

    def __str__(self) -> str:
        return "Not Applied"


@dataclass
class Success:
    """The extension was applied successfully."""

    def __str__(self) -> str:
        return "Success"


@dataclass
class Error:
    """An error occurred during the extension application."""

    message: str
    exception: str | None = None

    def __str__(self) -> str:
        return "Error"


ExtensionState = Union[NotApplied, Success, Error]


@dataclass
class BaseExtension:
    def set_state(self, state: ExtensionState) -> None:
        self.state = state


@dataclass
class UnknownExtension(BaseExtension):
    """An unknown extension.

    Likely the CLI should be updated.
    """

    type: str
    state: ExtensionState = field(default_factory=NotApplied)

    @property
    def details(self) -> list[str]:
        return [self.type]


@dataclass
class AddPatch(TypedDict):
    operation: Literal["add"]
    path: list[str | int]
    value: Any


@dataclass
class RemovePatch(TypedDict):
    operation: Literal["remove"]
    path: list[str | int]


Patch = Union[AddPatch, RemovePatch]


@dataclass
class SchemaPatchesExtension(BaseExtension):
    """Update the schema with its optimized version."""

    patches: list[Patch]
    state: ExtensionState = field(default_factory=NotApplied)

    @property
    def details(self) -> list[str]:
        return []


class TransformFunctionDefinition(TypedDict):
    kind: Literal["map", "filter"]
    name: str
    arguments: dict[str, Any]


@dataclass
class StrategyDefinition:
    name: str
    transforms: list[TransformFunctionDefinition] | None = None
    arguments: dict[str, Any] | None = None


@dataclass
class OpenApiStringFormatsExtension(BaseExtension):
    """Custom string formats."""

    formats: dict[str, list[StrategyDefinition]]
    state: ExtensionState = field(default_factory=NotApplied)

    @classmethod
    def from_dict(cls, formats: dict[str, list[dict[str, Any]]]) -> OpenApiStringFormatsExtension:
        return cls(formats={name: [StrategyDefinition(**item) for item in value] for name, value in formats.items()})

    @property
    def details(self) -> list[str]:
        count = len(self.formats)
        noun = "formats" if count > 1 else "format"
        return [f"{count} Open API {noun}"]


@dataclass
class GraphQLScalarsExtension(BaseExtension):
    """Custom scalars."""

    scalars: dict[str, list[StrategyDefinition]]
    state: ExtensionState = field(default_factory=NotApplied)

    @classmethod
    def from_dict(cls, formats: dict[str, list[dict[str, Any]]]) -> GraphQLScalarsExtension:
        return cls(scalars={name: [StrategyDefinition(**item) for item in value] for name, value in formats.items()})

    @property
    def details(self) -> list[str]:
        count = len(self.scalars)
        noun = "scalars" if count > 1 else "scalar"
        return [f"{count} GraphQL {noun}"]


# A CLI extension that can be used to adjust the behavior of Schemathesis.
Extension = Union[SchemaPatchesExtension, OpenApiStringFormatsExtension, GraphQLScalarsExtension, UnknownExtension]


def extension_from_dict(data: dict[str, Any]) -> Extension:
    if data["type"] == "schema_patches":
        return SchemaPatchesExtension(patches=data["patches"])
    elif data["type"] == "string_formats":
        return OpenApiStringFormatsExtension.from_dict(formats=data["items"])
    return UnknownExtension(type=data["type"])


@dataclass
class AnalysisSuccess:
    id: str
    message: str
    extensions: list[Extension]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalysisSuccess:
        return cls(
            id=data["id"],
            message=data["message"],
            extensions=[extension_from_dict(ext) for ext in data["extensions"]],
        )


@dataclass
class AnalysisError:
    message: str


AnalysisResult = Union[AnalysisSuccess, AnalysisError]
