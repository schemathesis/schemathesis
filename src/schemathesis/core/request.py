from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

if TYPE_CHECKING:
    import httpx
    import requests
    from werkzeug.wrappers import Request as WerkzeugRequest


def _parse_cookie_header(header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in header.split(";"):
        if "=" in part:
            k, _, v = part.strip().partition("=")
            cookies[k.strip()] = v.strip()
    return cookies


def _split_url(url: str) -> tuple[str, dict[str, list[str]]]:
    """Return (path, query_dict) from a full URL string."""
    parts = urlsplit(url)
    return parts.path, parse_qs(parts.query, keep_blank_values=True)


@dataclass(frozen=True)
class ParsedRequest:
    """Normalised view of an HTTP request, independent of transport library."""

    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    cookies: dict[str, str]
    body: bytes
    content_type: str | None

    @classmethod
    def from_any(cls, request: Any) -> ParsedRequest:
        import httpx
        import requests
        from werkzeug.wrappers import Request as WerkzeugRequest

        if isinstance(request, requests.PreparedRequest):
            return cls._from_prepared(request)
        if isinstance(request, httpx.Request):
            return cls._from_httpx(request)
        if isinstance(request, WerkzeugRequest):
            return cls._from_werkzeug(request)
        # Django is an optional dependency — guard the import
        try:
            from django.http import HttpRequest as DjangoHttpRequest

            if isinstance(request, DjangoHttpRequest):
                return cls._from_django(request)
        except ImportError:
            pass
        raise TypeError(f"Unsupported request type: {type(request)!r}")

    @classmethod
    def _from_prepared(cls, r: requests.PreparedRequest) -> ParsedRequest:
        path, query = _split_url(r.url or "")
        body = r.body if isinstance(r.body, bytes) else (r.body.encode() if r.body else b"")
        headers = dict(r.headers or {})
        return cls(
            method=r.method or "GET",
            path=path,
            query=query,
            headers=headers,
            cookies=_parse_cookie_header(headers.get("Cookie", "")),
            body=body,
            content_type=headers.get("Content-Type"),
        )

    @classmethod
    def _from_httpx(cls, r: httpx.Request) -> ParsedRequest:
        raw_query = r.url.query
        qs = raw_query.decode() if isinstance(raw_query, bytes) else str(raw_query)
        headers = dict(r.headers)
        return cls(
            method=r.method,
            path=r.url.path,
            query=parse_qs(qs, keep_blank_values=True),
            headers=headers,
            cookies=_parse_cookie_header(headers.get("cookie", "")),
            body=r.content,
            content_type=headers.get("content-type"),
        )

    @classmethod
    def _from_werkzeug(cls, r: WerkzeugRequest) -> ParsedRequest:
        qs = r.query_string.decode() if isinstance(r.query_string, bytes) else r.query_string
        return cls(
            method=r.method,
            path=r.path,
            query=parse_qs(qs, keep_blank_values=True),
            headers=dict(r.headers),
            cookies=dict(r.cookies),
            body=r.get_data(as_text=False),
            content_type=r.content_type or None,
        )

    @classmethod
    def _from_django(cls, r: Any) -> ParsedRequest:
        query: dict[str, list[str]] = {k: r.GET.getlist(k) for k in r.GET}
        headers: dict[str, str] = {}
        for key, value in r.META.items():
            if key.startswith("HTTP_"):
                headers[key[5:].replace("_", "-").title()] = value
            elif key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
                headers[key.replace("_", "-").title()] = value
        try:
            body = r.body
        except Exception:
            body = b""
        return cls(
            method=r.method,
            path=r.path,
            query=query,
            headers=headers,
            cookies=dict(r.COOKIES),
            body=body,
            content_type=r.META.get("CONTENT_TYPE") or None,
        )
