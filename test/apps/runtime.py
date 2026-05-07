from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, Literal, Protocol, TypeVar

from flask import Flask

if TYPE_CHECKING:
    from fastapi import FastAPI

Schema = dict[str, Any]

StoreT = TypeVar("StoreT")


class AppRunner(Protocol):
    def run_flask_app(self, app: Flask, port: int | None = None, timeout: float = 5.0, wait: bool = True) -> int: ...
    def run_asgi_app(self, app: FastAPI, port: int | None = None, timeout: float = 5.0, wait: bool = True) -> int: ...


@dataclass(slots=True)
class CapturedRequest:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes
    raw_query: str = ""

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass(slots=True)
class OpenAPIServer:
    schema_url: str
    base_url: str
    port: int
    spec: Schema
    wsgi_app: Flask | FastAPI
    requests: list[CapturedRequest] = field(default_factory=list)
    schema_requests: list[CapturedRequest] = field(default_factory=list)


@dataclass(slots=True)
class GraphQLServer:
    schema_url: str
    base_url: str
    port: int
    wsgi_app: Flask | FastAPI


@dataclass(slots=True)
class OpenAPIApp:
    spec: Schema
    server: Flask | FastAPI
    kind: Literal["flask", "fastapi"] = "flask"

    def make_server(self, port: int) -> OpenAPIServer:
        base_url = f"http://127.0.0.1:{port}"
        captured: list[CapturedRequest] = []
        schema_requests: list[CapturedRequest] = []
        if isinstance(self.server, Flask):
            captured = self.server.config.setdefault("captured_requests", captured)
            schema_requests = self.server.config.setdefault("captured_schema_requests", schema_requests)
        return OpenAPIServer(
            schema_url=f"{base_url}/openapi.json",
            base_url=base_url,
            port=port,
            spec=self.spec,
            wsgi_app=self.server,
            requests=captured,
            schema_requests=schema_requests,
        )


@dataclass(slots=True)
class GraphQLApp:
    server: Flask | FastAPI
    kind: Literal["flask", "fastapi"] = "flask"
    endpoint: str = "/graphql"

    def make_server(self, port: int) -> GraphQLServer:
        url = f"http://127.0.0.1:{port}{self.endpoint}"
        return GraphQLServer(schema_url=url, base_url=url, port=port, wsgi_app=self.server)


class Modifier(Protocol, Generic[StoreT]):
    priority: int

    def apply(self, app: Flask, store: StoreT) -> None: ...
