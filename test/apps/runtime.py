from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Literal, Protocol, TypeVar

if TYPE_CHECKING:
    from fastapi import FastAPI
    from flask import Flask

Schema = dict[str, Any]

StoreT = TypeVar("StoreT")


class AppRunner(Protocol):
    def run_flask_app(self, app: Flask, port: int | None = None, timeout: float = 0.05) -> int: ...
    def run_asgi_app(self, app: FastAPI, port: int | None = None, timeout: float = 0.05) -> int: ...


@dataclass(slots=True)
class OpenAPIServer:
    schema_url: str
    base_url: str
    port: int
    spec: Schema
    wsgi_app: Flask | FastAPI


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
        return OpenAPIServer(
            schema_url=f"{base_url}/openapi.json",
            base_url=base_url,
            port=port,
            spec=self.spec,
            wsgi_app=self.server,
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
