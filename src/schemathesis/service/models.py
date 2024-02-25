from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Union


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
class SchemaValidationError:
    title: str
    message: str
    extras: list[str]
    suggestion: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaValidationError:
        return cls(
            title=data["title"],
            message=data["message"],
            extras=data["extras"],
            suggestion=data["suggestion"],
        )


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
class SchemaOptimizationExtension(BaseExtension):
    """Update the schema with its optimized version."""

    schema: dict[str, Any]
    state: ExtensionState = field(default_factory=NotApplied)

    @property
    def details(self) -> list[str]:
        return []


@dataclass
class StringFormatsExtension(BaseExtension):
    """Custom string formats."""

    formats: dict[str, dict[str, Any]]
    state: ExtensionState = field(default_factory=NotApplied)

    @property
    def details(self) -> list[str]:
        counter: dict[str, int] = Counter()
        for format_definition in self.formats.values():
            if "regex" in format_definition:
                counter["format"] += 1
            if "samples" in format_definition:
                counter["example"] += len(format_definition["samples"])
        return [f"{count} {key}" if count == 1 else f"{count} {key}s" for key, count in counter.items()]


# A CLI extension that can be used to adjust the behavior of Schemathesis.
Extension = Union[SchemaOptimizationExtension, StringFormatsExtension, UnknownExtension]


def extension_from_dict(data: dict[str, Any]) -> Extension:
    if data["type"] == "schema":
        return SchemaOptimizationExtension(schema=data["schema"])
    elif data["type"] == "string_formats":
        return StringFormatsExtension(formats=data["formats"])
    return UnknownExtension(type=data["type"])


@dataclass
class AnalysisResult:
    id: str
    message: str
    extensions: list[Extension]
    validation_errors: list[SchemaValidationError]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalysisResult:
        return cls(
            id=data["id"],
            message=data["message"],
            extensions=[extension_from_dict(ext) for ext in data["extensions"]],
            validation_errors=[SchemaValidationError.from_dict(error) for error in data["validation_errors"]],
        )
