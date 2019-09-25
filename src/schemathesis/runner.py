from typing import Callable, NoReturn
from urllib.parse import urlsplit, urlunsplit

import requests

from .loaders import from_uri
from .models import Case


def execute(schema_uri: str) -> NoReturn:
    with requests.Session() as session:
        schema = from_uri(schema_uri)
        base_url = get_base_url(schema_uri)
        for endpoint, test in schema.get_all_tests(single_test):
            test(session, base_url, not_a_server_error)


def get_base_url(uri: str) -> str:
    parts = urlsplit(uri)[:2] + ("", "", "")
    return urlunsplit(parts)


def single_test(case: Case, session: requests.Session, url: str, check: Callable) -> NoReturn:
    response = get_response(session, url, case)
    check(response)


def get_response(session: requests.Session, url: str, case: Case) -> requests.Response:
    return session.request(
        case.method, f"{url}{case.formatted_path}", headers=case.headers, params=case.query, json=case.body
    )


def not_a_server_error(response: requests.Response) -> NoReturn:
    assert response.status_code < 500
