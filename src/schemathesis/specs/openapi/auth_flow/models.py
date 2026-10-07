from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class AuthFlowSpec:
    register_operation: str
    login_operation: str
    login_path: str
    login_media_type: str
    # Login body properties that carry credentials, sorted by name.
    credentials: tuple[str, ...]
    # JSON Pointer to the token in the login response body.
    token_pointer: str
    target_scheme: str
