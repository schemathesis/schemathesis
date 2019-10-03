from contextlib import suppress
from typing import Any, Callable, Dict, Iterable, Optional, Tuple, Union
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.auth import AuthBase

from .loaders import from_uri
from .models import Case
from .schemas import BaseSchema

Auth = Union[Tuple[str, str], AuthBase]


def not_a_server_error(response: requests.Response) -> None:
    """A check to verify that the response is not a server-side error."""
    assert response.status_code < 500


DEFAULT_CHECKS = (not_a_server_error,)


def _execute_all_tests(
    schema: BaseSchema,
    base_url: str,
    checks: Iterable[Callable],
    auth: Optional[Auth] = None,
    headers: Optional[Dict[str, Any]] = None,
) -> None:
    with requests.Session() as session, suppress(AssertionError):
        if auth is not None:
            session.auth = auth
        if headers is not None:
            session.headers.update(**headers)
        for _, test in schema.get_all_tests(single_test):
            test(session, base_url, checks)


def execute(
    schema_uri: str,
    base_url: str = "",
    checks: Iterable[Callable] = DEFAULT_CHECKS,
    auth: Optional[Auth] = None,
    headers: Optional[Dict[str, Any]] = None,
) -> None:
    """Generate and run test cases against the given API definition."""
    schema = from_uri(schema_uri)
    base_url = base_url or get_base_url(schema_uri)
    _execute_all_tests(schema, base_url, checks, auth, headers)


def get_base_url(uri: str) -> str:
    """Remove the path part off the given uri."""
    parts = urlsplit(uri)[:2] + ("", "", "")
    return urlunsplit(parts)


def single_test(case: Case, session: requests.Session, url: str, checks: Iterable[Callable]) -> None:
    """A single test body that will be executed against the target."""
    response = get_response(session, url, case)
    for check in checks:
        check(response)


def get_response(session: requests.Session, url: str, case: Case) -> requests.Response:
    """Send an appropriate request to the target."""
    return session.request(
        case.method, f"{url}{case.formatted_path}", headers=case.headers, params=case.query, json=case.body
    )
