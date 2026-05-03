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
class OpenAPIApp:
    spec: Schema
    server: Flask | FastAPI
    kind: Literal["flask", "fastapi"] = "flask"


@dataclass(slots=True)
class GraphQLApp:
    sdl: str
    server: Flask | FastAPI
    kind: Literal["flask", "fastapi"] = "flask"
    endpoint: str = "/graphql"


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
    sdl: str


class Modifier(Protocol, Generic[StoreT]):
    priority: int

    def apply(self, app: Flask, store: StoreT) -> None: ...
