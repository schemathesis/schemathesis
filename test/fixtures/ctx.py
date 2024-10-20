from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest


@dataclass
class OpenApiContext:
    def build_schema(self, paths: dict[str, Any], version: str = "3.0.2", **kwargs: dict[str, Any]) -> dict[str, Any]:
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


@dataclass
class Context:
    """Helper to localize common actions for testing API schemas."""

    @property
    def openapi(self) -> OpenApiContext:
        return OpenApiContext()


@pytest.fixture
def ctx() -> Context:
    return Context()
