import json
from pathlib import Path
from dataclasses import asdict

import pytest
from referencing.jsonschema import DRAFT4
from referencing import Registry, Resource

from schemathesis.internal.result import Ok
from schemathesis.specs.openapi._v2 import iter_operations
from schemathesis.specs.openapi._jsonschema import MOVED_REFERENCE_ROOT_KEY, MOVED_REFERENCE_KEY_LENGTH
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


def find_references_from_root(schema, resolver) -> set[str]:
    found = set()
    _find_references(schema, resolver, found)
    return found


def _find_references(item, resolver, found) -> None:
    if isinstance(item, dict):
        ref = item.get("$ref")
        if isinstance(ref, str):
            if ref in found:
                return
            found.add(ref)
            resolved = resolver.lookup(ref)
            resolver = resolved.resolver
            _find_references(resolved.contents, resolver, found)
        else:
            for key, sub_item in item.items():
                if key != MOVED_REFERENCE_ROOT_KEY:
                    _find_references(sub_item, resolver, found)
    elif isinstance(item, list):
        for sub_item in item:
            _find_references(sub_item, resolver, found)


def assert_no_unused_components(schema):
    if MOVED_REFERENCE_ROOT_KEY in schema:
        registry = Registry().with_resource("", Resource(contents=schema, specification=DRAFT4))
        resolver = registry.resolver()
        references = {ref[MOVED_REFERENCE_KEY_LENGTH:] for ref in find_references_from_root(schema, resolver)}
        assert not set(schema[MOVED_REFERENCE_ROOT_KEY]) - references


@pytest.mark.parametrize(
    "spec",
    [
        "empty",
        "basic",
        "form-data",
        "no-definitions",
        "shared-params",
        "ref-params",
        "ref-path-item",
        "with-jsonschema-extensions",
        "recursive-with-non-recursive",
        "recursive-with-list",
        "complex-definitions",
    ],
    indirect=True,
)
def test_iter_operations(spec, snapshot_json, assert_generates):
    for operation in iter_operations(spec, ""):
        assert isinstance(operation, Ok)
        operation = operation.ok()
        assert asdict(operation) == snapshot_json
        for param in operation.body + operation.headers + operation.path_parameters + operation.query:
            assert_generates(param.schema)
            # assert_no_unused_components(param.schema)
    list(iter_operations(spec, ""))
