from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from schemathesis.config import SanitizationConfig
from schemathesis.core import SCHEMATHESIS_TEST_CASE_HEADER, NotSet
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.output.sanitization import sanitize_url, sanitize_value
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import USER_AGENT
from schemathesis.generation.meta import CoveragePhaseData, CoverageScenario, FuzzingPhaseData, StatefulPhaseData

if TYPE_CHECKING:
    from requests import PreparedRequest
    from requests.structures import CaseInsensitiveDict

    from schemathesis.generation.case import Case


@lru_cache
def get_default_headers() -> CaseInsensitiveDict:
    from requests.utils import default_headers

    headers = default_headers()
    headers["User-Agent"] = USER_AGENT
    return headers


def prepare_headers(case: Case, headers: dict[str, str] | None = None) -> CaseInsensitiveDict:
    default_headers = get_default_headers().copy()
    if case.headers:
        default_headers.update(case.headers)
    default_headers.setdefault(SCHEMATHESIS_TEST_CASE_HEADER, case.id)
    if headers:
        default_headers.update(headers)
    return default_headers


def get_exclude_headers(case: Case) -> list[str]:
    if case.meta is None:
        return []

    phase_data = case.meta.phase.data

    # Exclude headers that are intentionally missing

    if (
        isinstance(phase_data, CoveragePhaseData)
        and phase_data.scenario == CoverageScenario.MISSING_PARAMETER
        and phase_data.parameter_location == ParameterLocation.HEADER
        and phase_data.parameter is not None
    ):
        return [phase_data.parameter]

    if (
        isinstance(phase_data, (FuzzingPhaseData, StatefulPhaseData))
        and case.meta.generation.mode.is_negative
        and phase_data.parameter_location == ParameterLocation.HEADER
        and phase_data.parameter is not None
    ):
        return [phase_data.parameter]

    return []


def prepare_url(case: Case, base_url: str | None) -> str:
    """Prepare URL based on case type."""
    from schemathesis.specs.graphql.schemas import GraphQLSchema

    base_url = base_url or case.operation.base_url
    assert base_url is not None
    path = prepare_path(case.path, case.path_parameters)

    if isinstance(case.operation.schema, GraphQLSchema):
        parts = list(urlsplit(base_url))
        parts[2] = path
        return urlunsplit(parts)
    else:
        path = path.lstrip("/")
        if not base_url.endswith("/"):
            base_url += "/"
        return unquote(urljoin(base_url, quote(path)))


def prepare_body(case: Case) -> list | dict[str, Any] | str | int | float | bool | bytes | NotSet:
    """Prepare body based on case type."""
    from schemathesis.specs.graphql.schemas import GraphQLSchema

    if isinstance(case.operation.schema, GraphQLSchema):
        return case.body if isinstance(case.body, (NotSet, bytes)) else {"query": case.body}
    return case.body


def normalize_base_url(base_url: str | None) -> str | None:
    """Normalize base URL by ensuring proper hostname for local URLs.

    If URL has no hostname (typical for WSGI apps), adds "localhost" as default hostname.
    """
    if base_url is None:
        return None
    parts = urlsplit(base_url)
    if not parts.hostname:
        path = cast(str, parts.path or "")
        return urlunsplit(("http", "localhost", path or "", "", ""))
    return base_url


def prepare_path(path: str, parameters: dict[str, Any] | None) -> str:
    try:
        return path.format(**parameters or {})
    except KeyError as exc:
        # This may happen when a path template has a placeholder for variable "X", but parameter "X" is not defined
        # in the parameters list.
        # When `exc` is formatted, it is the missing key name in quotes. E.g. 'id'
        raise InvalidSchema(f"Path parameter {exc} is not defined") from exc
    except (IndexError, ValueError) as exc:
        # A single unmatched `}` inside the path template may cause this
        raise InvalidSchema(f"Malformed path template: `{path}`\n\n  {exc}") from exc


def prepare_request(case: Case, headers: Mapping[str, Any] | None, *, config: SanitizationConfig) -> PreparedRequest:
    import requests

    from schemathesis.transport.requests import REQUESTS_TRANSPORT

    base_url = normalize_base_url(case.operation.base_url)
    kwargs = REQUESTS_TRANSPORT.serialize_case(case, base_url=base_url, headers=headers)
    if config.enabled:
        kwargs["url"] = sanitize_url(kwargs["url"], config=config)
        kwargs["headers"] = dict(kwargs["headers"])
        sanitize_value(kwargs["headers"], config=config)
        if kwargs["cookies"]:
            kwargs["cookies"] = dict(kwargs["cookies"])
            sanitize_value(kwargs["cookies"], config=config)
        if kwargs["params"]:
            kwargs["params"] = dict(kwargs["params"])
            sanitize_value(kwargs["params"], config=config)

    return requests.Request(**kwargs).prepare()
