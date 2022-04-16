from typing import Optional

import attr


@attr.s(slots=True)
class ApiConfig:
    location: str = attr.ib()
    base_url: Optional[str] = attr.ib()


@attr.s(slots=True)
class TestRun:
    run_id: str = attr.ib()
    short_url: str = attr.ib()
    config: ApiConfig = attr.ib()


@attr.s(slots=True)
class AuthResponse:
    username: str = attr.ib()
