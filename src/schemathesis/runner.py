from typing import Callable, Iterator
from urllib.parse import urlsplit, urlunsplit

import requests

from .loaders import from_uri
from .models import Case


def not_a_server_error(response: requests.Response) -> None:
    """A check to verify that the response is not a server-side error."""
    assert response.status_code < 500


def execute(schema_uri: str, checks=(not_a_server_error,)) -> None:
    """Generate and run test cases against the given API definition."""
    with requests.Session() as session:
        schema = from_uri(schema_uri)
        base_url = get_base_url(schema_uri)
        for _, test in schema.get_all_tests(single_test):
            test(session, base_url, checks)


def get_base_url(uri: str) -> str:
    """Remove the path part off the given uri."""
    parts = urlsplit(uri)[:2] + ("", "", "")
    return urlunsplit(parts)


def single_test(case: Case, session: requests.Session, url: str, checks: Iterator[Callable]) -> None:
    """A single test body that will be executed against the target."""
    response = get_response(session, url, case)
    for check in checks:
        check(response)


def get_response(session: requests.Session, url: str, case: Case) -> requests.Response:
    """Send an appropriate request to the target."""
    return session.request(
        case.method, f"{url}{case.formatted_path}", headers=case.headers, params=case.query, json=case.body
    )
