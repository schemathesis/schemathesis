from __future__ import annotations

import sys
import tempfile
from io import StringIO
from pathlib import Path

import pytest

from schemathesis.config import ProjectConfig, SanitizationConfig
from schemathesis.reporting import HarWriter, JunitXmlWriter, NdjsonWriter, VcrWriter
from schemathesis.reporting._command import get_command_representation


def test_command_representation_sanitizes_credentials(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["st", "run", "schema.yaml", "--auth", "user:secret", "--header", "Authorization: Bearer token"],
    )

    command = get_command_representation(sanitization=SanitizationConfig())

    assert command == "st run schema.yaml --auth [Filtered] --header Authorization: [Filtered]"


def test_command_representation_sanitizes_glued_short_options(monkeypatch):
    # Click accepts a short option glued to its value (`-aVALUE`); that form must be redacted too.
    monkeypatch.setattr(
        sys,
        "argv",
        ["st", "run", "schema.yaml", "-auser:secret", "-HAuthorization: Bearer token"],
    )

    command = get_command_representation(sanitization=SanitizationConfig())

    assert "user:secret" not in command
    assert "Bearer token" not in command


def test_ndjson_writer_context_manager():
    stream = StringIO()
    with NdjsonWriter(output=stream) as writer:
        writer.open(seed=42, command="st run http://localhost/openapi.json")
    content = stream.getvalue()
    assert '"Initialize"' in content
    assert '"seed":42' in content


def test_ndjson_writer_context_manager_closes_file():
    with tempfile.NamedTemporaryFile(suffix=".ndjson", delete=False) as f:
        path = Path(f.name)
    try:
        with NdjsonWriter(output=path) as writer:
            writer.open(seed=1, command="st run http://localhost/openapi.json")
        assert path.stat().st_size > 0
    finally:
        path.unlink(missing_ok=True)


def test_junitxml_writer_context_manager():
    stream = StringIO()
    with JunitXmlWriter(output=stream) as writer:
        writer.record_error("test_label", "something went wrong")
    content = stream.getvalue()
    assert "schemathesis" in content
    assert "test_label" in content


@pytest.mark.parametrize(
    "writer_cls, kwargs",
    [
        (VcrWriter, {"config": ProjectConfig.from_dict({})}),
        (HarWriter, {"config": ProjectConfig.from_dict({})}),
    ],
    ids=["vcr", "har"],
)
def test_writer_context_manager_no_error_without_open(writer_cls, kwargs):
    with writer_cls(output=StringIO(), **kwargs):
        pass
