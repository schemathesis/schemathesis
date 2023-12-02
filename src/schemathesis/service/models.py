from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class UploadSource(str, Enum):
    DEFAULT = "default"
    UPLOAD_COMMAND = "upload_command"


@dataclass
class ApiDetails:
    location: str
    base_url: str | None


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
