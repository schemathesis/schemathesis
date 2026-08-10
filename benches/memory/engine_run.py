import json
import pathlib
import sys
from typing import Any
from unittest.mock import patch

import pytest
import requests
import yaml

import schemathesis
from schemathesis.config import HealthCheck
from schemathesis.core.transport import Response
from schemathesis.engine import from_schema

CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
sys.path.append(str(CURRENT_DIR.parent.parent))

RESPONSE = Response(
    status_code=200,
    headers={"Content-Type": ["application/json"]},
    content=b"{}",
    request=requests.Request(method="GET", url="http://127.0.0.1/test").prepare(),
    elapsed=0.1,
    verify=False,
)
patch("schemathesis.Case.call", return_value=RESPONSE).start()


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CURRENT_DIR.parent / "schemas" / f"{name}.yaml").read_text())


def _wide_properties(block_count: int, field_count: int) -> dict[str, Any]:
    properties = {}
    for block in range(block_count):
        fields = {
            f"field_{block}_{index}": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": index % 5, "maxLength": 40 + index},
                    "count": {"type": "integer", "minimum": index, "maximum": 1000 + index},
                    "tags": {"type": "array", "items": {"type": "string", "pattern": f"^tag{index}[a-z]+$"}},
                    "nested": {
                        "type": "object",
                        "properties": {f"leaf_{leaf}": {"type": "string", "format": "uuid"} for leaf in range(6)},
                        "required": [f"leaf_{leaf}" for leaf in range(3)],
                    },
                },
                "required": ["name", "count"],
            }
            for index in range(field_count)
        }
        properties[f"block_{block}"] = {"type": "object", "properties": fields, "required": list(fields)[:4]}
    return {
        "openapi": "3.0.3",
        "info": {"title": "Wide Properties API", "version": "1.0.0"},
        "paths": {
            "/api/v1/records": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object", "properties": properties}}},
                    },
                    "responses": {"201": {"description": "Created"}, "422": {"description": "Validation error"}},
                }
            }
        },
    }


LARGE_PAYLOAD = _load("large_payload")
STATEFUL_USERS = _load("stateful_users")
WIDE_PROPERTIES = _wide_properties(12, 10)
# The CLI path costs ~60x the engine path per case, so its variant is sized down to match.
WIDE_PROPERTIES_CLI = _wide_properties(3, 4)


def _execute(raw_schema: dict[str, Any], phase: str) -> None:
    schema = schemathesis.openapi.from_dict(raw_schema)
    schema.config.update(base_url="http://127.0.0.1:8080/", suppress_health_check=list(HealthCheck))
    schema.config.phases.update(phases=[phase])
    for _ in from_schema(schema).execute():
        pass


@pytest.mark.benchmark(group="engine-run")
def test_large_payload_fuzzing(benchmark):
    benchmark(_execute, LARGE_PAYLOAD, "fuzzing")


@pytest.mark.benchmark(group="engine-run")
def test_large_payload_coverage(benchmark):
    benchmark(_execute, LARGE_PAYLOAD, "coverage")


@pytest.mark.benchmark(group="engine-run")
def test_wide_properties_coverage(benchmark):
    benchmark(_execute, WIDE_PROPERTIES, "coverage")


@pytest.mark.benchmark(group="engine-run")
def test_stateful_users(benchmark):
    benchmark(_execute, STATEFUL_USERS, "stateful")


def _run_cli(path: str) -> None:
    from click.testing import CliRunner

    from schemathesis.cli import schemathesis as cli

    CliRunner().invoke(
        cli,
        ["run", str(path), "--phases=coverage", "--continue-on-failure", "-u", "http://127.0.0.1:8080/", "--seed=1"],
        catch_exceptions=False,
    )


@pytest.mark.benchmark(group="cli-run")
def test_wide_properties_cli(benchmark, tmp_path_factory):
    path = tmp_path_factory.mktemp("schemas") / "wide_properties.json"
    path.write_text(json.dumps(WIDE_PROPERTIES_CLI))
    benchmark(_run_cli, path)
