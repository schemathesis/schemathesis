from typing import Optional

import attr


@attr.s(slots=True)
class ApiDetails:
    location: str = attr.ib()
    base_url: Optional[str] = attr.ib()


@attr.s(slots=True)
class AuthResponse:
    username: str = attr.ib()


@attr.s(slots=True)
class UploadResponse:
    message: str = attr.ib()
    next_url: str = attr.ib()
    correlation_id: str = attr.ib()


@attr.s(slots=True)
class FailedUploadResponse:
    detail: str = attr.ib()
