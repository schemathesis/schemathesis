"""Helpers for sending follow-up requests with modified or stripped authentication."""

from __future__ import annotations

from collections.abc import Mapping
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any

from schemathesis.generation.case import Case

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation


def set_auth_for_case(case: Case, parameter: Mapping[str, Any]) -> None:
    """Attach a security parameter's value to the case in-place."""
    name = parameter["name"]
    for location, attr_name in (
        ("header", "headers"),
        ("query", "query"),
        ("cookie", "cookies"),
    ):
        if parameter["in"] == location:
            container = getattr(case, attr_name, {})
            # Negative-mode cases may replace the container with a non-dict; reset to a fresh dict.
            if not isinstance(container, dict):
                container = {}
            container[name] = "SCHEMATHESIS-INVALID-VALUE"
            setattr(case, attr_name, container)


def get_security_parameters(operation: APIOperation) -> list[Mapping[str, Any]]:
    """Extract security definitions that are active for the given operation and convert them into parameters."""
    # Lazy-imported: this module is loaded on CLI startup via `checks.py`; the adapter
    # transitively pulls in the schema/auth machinery, which we want to defer.
    from schemathesis.specs.openapi.adapter.security import ORIGINAL_SECURITY_TYPE_KEY

    return [
        param
        for param in operation.security.iter_parameters()
        if param[ORIGINAL_SECURITY_TYPE_KEY] in ["apiKey", "basic", "http"]
    ]


def remove_auth_from_container(container: dict, security_parameters: list[Mapping[str, Any]], location: str) -> None:
    """Strip auth keys from a transport-kwargs container in-place."""
    for parameter in security_parameters:
        name = parameter["name"]
        if parameter["in"] == location:
            container.pop(name, None)


def remove_auth(case: Case, security_parameters: list[Mapping[str, Any]]) -> Case:
    """Return a copy of `case` with the listed security parameters scrubbed; the new case has a fresh id."""
    headers = case.headers.copy()
    query = case.query.copy()
    cookies = case.cookies.copy()
    for parameter in security_parameters:
        name = parameter["name"]
        if parameter["in"] == "header" and headers:
            headers.pop(name, None)
        if parameter["in"] == "query" and query:
            query.pop(name, None)
        if parameter["in"] == "cookie":
            if cookies:
                cookies.pop(name, None)
            if headers and "Cookie" in headers:
                parsed: SimpleCookie = SimpleCookie(headers["Cookie"])
                parsed.pop(name, None)
                if parsed:
                    headers["Cookie"] = "; ".join(f"{k}={v.coded_value}" for k, v in parsed.items())
                else:
                    del headers["Cookie"]
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
    security_parameters: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a copy of `base_kwargs` with auth and session entries stripped."""
    kwargs: dict[str, Any] = (base_kwargs or {}).copy()
    # Query parameters can ride either the schemathesis-style `query` or the requests-style `params`.
    for location, container_name in (
        ("header", "headers"),
        ("cookie", "cookies"),
        ("query", "query"),
        ("query", "params"),
    ):
        if container_name in kwargs:
            container = kwargs[container_name]
            if isinstance(container, dict):
                container = container.copy()
                remove_auth_from_container(container, security_parameters, location=location)
                kwargs[container_name] = container
    kwargs.pop("session", None)
    kwargs.pop("auth", None)
    return kwargs
