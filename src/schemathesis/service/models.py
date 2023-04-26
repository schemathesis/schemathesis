from dataclasses import dataclass
from typing import Optional


@dataclass
class ApiDetails:
    location: str
    base_url: Optional[str]


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
