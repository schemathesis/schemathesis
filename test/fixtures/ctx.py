from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from textwrap import dedent
from typing import Any

import pytest
import yaml
from flask import Flask

from schemathesis.checks import CHECKS
from schemathesis.hooks import GLOBAL_HOOK_DISPATCHER
from test.apps import builders
from test.apps.catalog.openapi import basic as openapi_basic
from test.apps.runtime import OpenAPIApp, OpenAPIServer


@dataclass(slots=True)
class OpenAPIApps:
    parent: Context

    def success(self) -> OpenAPIServer:
        return self._start(openapi_basic.success())

    def _start(self, app: OpenAPIApp) -> OpenAPIServer:
        app_runner = self.parent.request.getfixturevalue("app_runner")
        if app.kind == "flask":
            port = app_runner.run_flask_app(app.server)
        else:
            port = app_runner.run_asgi_app(app.server)
        return OpenAPIServer(
            schema_url=f"http://127.0.0.1:{port}/openapi.json",
            base_url=f"http://127.0.0.1:{port}",
            port=port,
            spec=app.spec,
        )


@dataclass
class OpenApiContext:
    parent: Context

    @property
    def apps(self) -> OpenAPIApps:
        return OpenAPIApps(parent=self.parent)

    def build_schema(
        self, paths: dict[str, Any], *, version: str = "3.0.2", **kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        return builders.build_schema(paths, version=version, **kwargs)

    def write_schema(
        self,
        paths: dict[str, Any],
        *,
        version: str = "3.0.2",
        format: str = "json",
        filename: str = "schema",
        **kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        schema = self.build_schema(paths, version=version, **kwargs)
        return self.parent.makefile(schema, format=format, filename=filename)

    def make_flask_app(
        self, paths: dict[str, Any], *, version: str = "3.0.2", **kwargs: dict[str, Any]
    ) -> tuple[Flask, dict[str, Any]]:
        return builders.make_flask_app(paths, version=version, **kwargs)

    def make_flask_app_from_schema(self, schema: dict[str, Any]) -> Flask:
        return builders.make_flask_app_from_schema(schema)


@dataclass
class Context:
    """Helper to localize common actions for testing API schemas."""

    request: pytest.FixtureRequest

    @property
    def openapi(self) -> OpenApiContext:
        return OpenApiContext(parent=self)

    @property
    def _testdir(self):
        return self.request.getfixturevalue("testdir")

    def makefile(
        self, schema: dict[str, Any], *, format: str = "json", filename: str = "schema", parent: str | None = None
    ):
        if parent is not None:
            directory = self._testdir.mkdir(parent)
        else:
            directory = self._testdir.tmpdir
        if format == "json":
            path = directory / f"{filename}.json"
            path.write_text(json.dumps(schema), "utf8")
            return path
        if format == "yaml":
            path = directory / f"{filename}.yaml"
            path.write_text(yaml.dump(schema), "utf8")
            return path
        raise ValueError(f"Unknown format: {format}")

    def write_pymodule(self, content: str, *, filename: str = "module"):
        content = f"import schemathesis\nnote=print\n{dedent(content)}"
        module = self._testdir.makepyfile(**{filename: content})
        pkgroot = module.dirpath()
        module._ensuresyspath(True, pkgroot)
        return module.purebasename

    @contextmanager
    def check(self, content: str):
        with self.restore_checks():
            yield self.write_pymodule(content)

    @contextmanager
    def restore_checks(self):
        names = set(CHECKS.get_all_names())
        try:
            yield
        finally:
            new_names = set(CHECKS.get_all_names()) - names
            for name in new_names:
                CHECKS.unregister(name)

    @contextmanager
    def hook(self, content: str):
        with self.restore_hooks():
            yield self.write_pymodule(content)

    @contextmanager
    def restore_hooks(self):
        before = GLOBAL_HOOK_DISPATCHER.get_all()
        try:
            yield
        finally:
            GLOBAL_HOOK_DISPATCHER._hooks = before


@pytest.fixture
def restore_checks(ctx):
    with ctx.restore_checks():
        yield


@pytest.fixture
def ctx(request) -> Context:
    return Context(request=request)
