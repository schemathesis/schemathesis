from collections import Counter, defaultdict
from contextlib import suppress
from typing import Any, Callable, Dict, Iterable, Optional, Tuple, Union
from urllib.parse import urlsplit, urlunsplit

import attr
import requests
from requests.auth import AuthBase

from .loaders import from_uri
from .models import Case
from .schemas import BaseSchema

Auth = Union[Tuple[str, str], AuthBase]


def _stats_data_factory() -> defaultdict:
    return defaultdict(Counter)


@attr.s(slots=True)
class StatsCollector:
    """A container for collected data from test executor."""

    data: Dict[str, Counter] = attr.ib(factory=_stats_data_factory)

    @property
    def is_empty(self) -> bool:
        return len(self.data) == 0

    def increment(self, check_name: str, error: Optional[Exception] = None) -> None:
        self.data[check_name]["total"] += 1
        self.data[check_name]["ok"] += error is None
        self.data[check_name]["error"] += error is not None


def not_a_server_error(response: requests.Response) -> None:
    """A check to verify that the response is not a server-side error."""
    assert response.status_code < 500


DEFAULT_CHECKS = (not_a_server_error,)


def execute_from_schema(
    schema: BaseSchema,
    base_url: str,
    checks: Iterable[Callable],
    auth: Optional[Auth] = None,
    headers: Optional[Dict[str, Any]] = None,
) -> StatsCollector:
    stats = StatsCollector()

    with requests.Session() as session:
        if auth is not None:
            session.auth = auth
        if headers is not None:
            session.headers.update(**headers)
        for _, test in schema.get_all_tests(single_test):
            with suppress(AssertionError):
                test(session, base_url, checks, stats)

    return stats


def execute(
    schema_uri: str,
    checks: Iterable[Callable] = DEFAULT_CHECKS,
    api_options: Optional[Dict[str, Any]] = None,
    loader_options: Optional[Dict[str, Any]] = None,
    loader: Callable = from_uri,
) -> StatsCollector:
    """Generate and run test cases against the given API definition."""
    api_options = api_options or {}
    loader_options = loader_options or {}

    schema = loader(schema_uri, **loader_options)
    base_url = api_options.pop("base_url", "") or get_base_url(schema_uri)
    return execute_from_schema(schema, base_url, checks, **api_options)


def get_base_url(uri: str) -> str:
    """Remove the path part off the given uri."""
    parts = urlsplit(uri)[:2] + ("", "", "")
    return urlunsplit(parts)


def single_test(
    case: Case, session: requests.Session, url: str, checks: Iterable[Callable], stats: StatsCollector
) -> None:
    """A single test body that will be executed against the target."""
    response = get_response(session, url, case)
    errors = {}

    for check in checks:
        check_name = check.__name__
        try:
            check(response)
            stats.increment(check_name)
        except AssertionError as e:
            stats.increment(check_name, error=e)
            errors[check_name] = e

    if errors:
        raise AssertionError("Assertion failed in: {}.".format(",".join(errors)))


def get_response(session: requests.Session, url: str, case: Case) -> requests.Response:
    """Send an appropriate request to the target."""
    return session.request(
        case.method, f"{url}{case.formatted_path}", headers=case.headers, params=case.query, json=case.body
    )
