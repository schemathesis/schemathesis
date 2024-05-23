import json
from pathlib import Path
from dataclasses import asdict

import pytest

from schemathesis.internal.result import Ok
from schemathesis.specs.openapi._v2 import iter_operations
from schemathesis.specs.openapi.definitions import SWAGGER_20_VALIDATOR

HERE = Path(__file__).parent.absolute()
SPEC_DIR = HERE / "specs"


def read_spec(name):
    with open(SPEC_DIR / name) as fd:
        return json.load(fd)


@pytest.fixture
def spec(request):
    spec = read_spec(f"{request.param}.json")
    SWAGGER_20_VALIDATOR.validate(spec)
    return spec


@pytest.mark.parametrize(
    "spec",
    ["empty", "basic", "form-data", "no-definitions", "shared-params", "ref-params", "ref-path-item"],
    indirect=True,
)
def test_iter_operations(spec, snapshot_json, assert_generates):
    for operation in iter_operations(spec, ""):
        assert isinstance(operation, Ok)
        operation = operation.ok()
        assert asdict(operation) == snapshot_json
        for param in operation.body + operation.headers + operation.path_parameters + operation.query:
            assert_generates(param.schema)
