from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any


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
