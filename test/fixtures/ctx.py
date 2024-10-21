from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
import yaml


@dataclass
class OpenApiContext:
    parent: Context

    def build_schema(
        self, paths: dict[str, Any], *, version: str = "3.0.2", **kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        """Create a new schema as a dict."""
        template: dict[str, Any] = {
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "paths": paths,
        }
        if version.startswith("3"):
            template["openapi"] = version
        elif version.startswith("2"):
            template["swagger"] = version
        else:
            raise ValueError("Unknown version")
        return {**template, **kwargs}

    def write_schema(
        self, paths: dict[str, Any], *, version: str = "3.0.2", format: str = "json", **kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        schema = self.build_schema(paths, version=version, **kwargs)
        return self.parent.makefile(schema, format=format)


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

    def makefile(self, schema: dict[str, Any], *, format: str = "json"):
        if format == "json":
            return self._testdir.makefile(".json", schema=json.dumps(schema))
        if format == "yaml":
            return self._testdir.makefile(".yaml", schema=yaml.dump(schema))
        raise ValueError(f"Unknown format: {format}")


@pytest.fixture
def ctx(request) -> Context:
    return Context(request=request)
