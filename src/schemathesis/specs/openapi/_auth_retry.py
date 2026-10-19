"""Helpers for sending follow-up requests with modified or stripped authentication."""

from __future__ import annotations

from base64 import b64encode
from collections.abc import MutableMapping
from http.cookies import CookieError, SimpleCookie
from typing import TYPE_CHECKING, Any

from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation.case import Case

if TYPE_CHECKING:
    from schemathesis.checks import CheckContext
    from schemathesis.core.transport import Response
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.adapter.security import SecurityParameter

INVALID_CREDENTIAL = "SCHEMATHESIS-INVALID-VALUE"
# Wrong credentials that still match the syntax their scheme expects, so the API runs its own
# credential validation instead of rejecting the value while parsing the `Authorization` header.
INVALID_AUTH_HEADERS = {
    "_basic_auth": f"Basic {b64encode(f'{INVALID_CREDENTIAL}:{INVALID_CREDENTIAL}'.encode()).decode()}",
    "_bearer_auth": f"Bearer {INVALID_CREDENTIAL}",
}
_CREDENTIAL_LOCATIONS = frozenset({ParameterLocation.HEADER, ParameterLocation.QUERY, ParameterLocation.COOKIE})
# Transport kwargs holding each credential location; query parameters ride either `query` or requests-style `params`.
_TRANSPORT_CONTAINERS = (
    ("headers", ParameterLocation.HEADER),
    ("cookies", ParameterLocation.COOKIE),
    ("query", ParameterLocation.QUERY),
    ("params", ParameterLocation.QUERY),
)


def _invalid_value_for(parameter: SecurityParameter) -> str:
    """The credential to probe a security parameter with."""
    # Swagger 2.0 keeps the parameter schema inline, Open API 3 nests it under `schema`.
    schema = parameter.definition.get("schema", parameter.definition)
    return INVALID_AUTH_HEADERS.get(schema.get("format", ""), INVALID_CREDENTIAL)


def set_auth_for_case(case: Case, parameter: SecurityParameter) -> None:
    """Attach a security parameter's value to the case in-place."""
    if parameter.location not in _CREDENTIAL_LOCATIONS:
        return
    attr_name = parameter.location.container_name
    container = getattr(case, attr_name)
    # Negative-mode cases may replace the container with a non-mapping; reset to a fresh dict.
    if not isinstance(container, MutableMapping):
        container = {}
    container[parameter.name] = _invalid_value_for(parameter)
    setattr(case, attr_name, container)


def get_security_parameters(operation: APIOperation) -> list[SecurityParameter]:
    """Extract security definitions that are active for the given operation and convert them into parameters."""
    return [param for param in operation.security.iter_parameters() if param.scheme_type in ["apiKey", "basic", "http"]]


def remove_auth_from_container(
    container: dict, security_parameters: list[SecurityParameter], location: ParameterLocation
) -> None:
    """Strip auth keys from a transport-kwargs container in-place."""
    for parameter in security_parameters:
        if parameter.location == location:
            container.pop(parameter.name, None)


def remove_auth_from_cookie_header(headers: MutableMapping[str, str], name: str) -> None:
    """Strip a cookie from the `Cookie` header in-place, whatever the header name's case."""
    for key in [key for key in headers if key.lower() == "cookie"]:
        parsed: SimpleCookie = SimpleCookie()
        try:
            parsed.load(headers[key])
        except CookieError:
            # Unparsable header: drop it entirely rather than risk keeping the auth cookie.
            del headers[key]
            continue
        parsed.pop(name, None)
        if parsed:
            headers[key] = "; ".join(f"{k}={v.coded_value}" for k, v in parsed.items())
        else:
            del headers[key]


def remove_auth(case: Case, security_parameters: list[SecurityParameter]) -> Case:
    """Return a copy of `case` with the listed security parameters scrubbed; the new case has a fresh id."""
    headers = case.headers.copy()
    query = case.query.copy()
    cookies = case.cookies.copy()
    for parameter in security_parameters:
        name = parameter.name
        if parameter.location == ParameterLocation.HEADER and headers:
            headers.pop(name, None)
        if parameter.location == ParameterLocation.QUERY and query:
            query.pop(name, None)
        if parameter.location == ParameterLocation.COOKIE:
            if cookies:
                cookies.pop(name, None)
            if headers:
                remove_auth_from_cookie_header(headers, name)
    return Case(
        operation=case.operation,
        method=case.method,
        path=case.path,
        path_parameters=case.path_parameters.copy(),
        headers=headers,
        cookies=cookies,
        query=query,
        body=case.body.copy() if isinstance(case.body, (list | dict)) else case.body,
        media_type=case.media_type,
        multipart_content_types=case.multipart_content_types,
        meta=case.meta,
    )


def clone_case(case: Case) -> Case:
    """Return a copy of `case` with a fresh id. No auth scrubbing — use `remove_auth` for that."""
    return remove_auth(case, [])


def build_retry_transport_kwargs(
    base_kwargs: dict[str, Any] | None,
    security_parameters: list[SecurityParameter],
) -> dict[str, Any]:
    """Return a copy of `base_kwargs` with auth and session entries stripped."""
    kwargs: dict[str, Any] = (base_kwargs or {}).copy()
    for container_name, location in _TRANSPORT_CONTAINERS:
        if container_name in kwargs:
            container = kwargs[container_name]
            if isinstance(container, dict):
                container = container.copy()
                remove_auth_from_container(container, security_parameters, location=location)
                kwargs[container_name] = container
    # A configured `Cookie` header would otherwise re-add the auth cookie to every retried request.
    headers = kwargs.get("headers")
    if isinstance(headers, dict):
        for parameter in security_parameters:
            if parameter.location == ParameterLocation.COOKIE:
                remove_auth_from_cookie_header(headers, parameter.name)
    kwargs.pop("session", None)
    kwargs.pop("auth", None)
    return kwargs


def send_probe(ctx: CheckContext, parent: Case, probe: Case) -> Response:
    """Send `probe` as a child of `parent`, keeping the run's transport settings but none of its credentials."""
    kwargs = build_retry_transport_kwargs(ctx._transport_kwargs, get_security_parameters(parent.operation))
    if parent.operation.app is not None:
        kwargs.setdefault("app", parent.operation.app)
    ctx._record_case(parent_id=parent.id, case=probe)
    response = parent.operation.schema.transport.send(probe, **kwargs)
    ctx._record_response(case_id=probe.id, response=response)
    return response
