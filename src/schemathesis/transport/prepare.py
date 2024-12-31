from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, cast
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from schemathesis.core import SCHEMATHESIS_TEST_CASE_HEADER, NotSet
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.output.sanitization import sanitize_url, sanitize_value
from schemathesis.core.transport import USER_AGENT

if TYPE_CHECKING:
    from requests import PreparedRequest
    from requests.structures import CaseInsensitiveDict

    from schemathesis.generation.case import Case


def prepare_headers(case: Case, headers: dict[str, str] | None = None) -> CaseInsensitiveDict:
    from requests.structures import CaseInsensitiveDict

    final_headers = case.headers.copy() if case.headers is not None else CaseInsensitiveDict()
    if headers:
        final_headers.update(headers)
    final_headers.setdefault("User-Agent", USER_AGENT)
    final_headers.setdefault(SCHEMATHESIS_TEST_CASE_HEADER, case.id)
    return final_headers


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


def prepare_request(case: Case, headers: Mapping[str, Any] | None, sanitize: bool) -> PreparedRequest:
    import requests

    from schemathesis.transport.requests import REQUESTS_TRANSPORT

    base_url = normalize_base_url(case.operation.base_url)
    kwargs = REQUESTS_TRANSPORT.serialize_case(case, base_url=base_url, headers=headers)
    if sanitize:
        kwargs["url"] = sanitize_url(kwargs["url"])
        sanitize_value(kwargs["headers"])
        if kwargs["cookies"]:
            sanitize_value(kwargs["cookies"])
        if kwargs["params"]:
            sanitize_value(kwargs["params"])

    return requests.Request(**kwargs).prepare()
