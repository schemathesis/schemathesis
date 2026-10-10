from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.auths import AuthContext
from schemathesis.specs.openapi._auth_retry import clone_case, remove_auth_from_cookie_header
from schemathesis.wfc.escalation import escalating_provider

if TYPE_CHECKING:
    from schemathesis.auths import AuthProvider
    from schemathesis.generation.case import Case


def _apply(case: Case, provider: AuthProvider) -> None:
    context = AuthContext(operation=case.operation, app=case.operation.app)
    provider.set(case, provider.get(case, context), context)


def owner_provider(case: Case) -> AuthProvider:
    provider = escalating_provider(case.operation.schema)
    assert provider is not None and case._auth_identity is not None
    return provider.provider_for(case._auth_identity)


def strip_credentials(case: Case, provider: AuthProvider) -> Case:
    """Copy `case` without anything `provider` puts on a request."""
    stripped = clone_case(case)
    # Applying the provider to an empty request reveals which keys it owns, whatever their names.
    probe = clone_case(case)
    probe.headers.clear()
    probe.query.clear()
    probe.cookies.clear()
    _apply(probe, provider)
    for name in probe.headers:
        stripped.headers.pop(name, None)
    for name in probe.query:
        stripped.query.pop(name, None)
    for name in probe.cookies:
        stripped.cookies.pop(name, None)
        remove_auth_from_cookie_header(stripped.headers, name)
    return stripped


def apply_as(case: Case, peer: AuthProvider, name: str) -> Case:
    """Copy `case` as if `name` had sent it."""
    probe = strip_credentials(case, owner_provider(case))
    _apply(probe, peer)
    probe._auth_identity = name
    return probe
