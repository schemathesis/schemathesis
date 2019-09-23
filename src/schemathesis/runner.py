from typing import Callable

import requests

from .models import Case
from .schemas import BaseSchema


def execute(url: str, schema: BaseSchema) -> None:
    def single_test(case: Case, check: Callable) -> None:
        response = requests.request(
            case.method, f"{url}{case.formatted_path}", headers=case.headers, params=case.query, json=case.body
        )
        check(response)

    for _, test in schema.get_all_tests(single_test):
        test(check_1)


def check_1(response: requests.Response) -> None:
    assert response.status_code < 500
