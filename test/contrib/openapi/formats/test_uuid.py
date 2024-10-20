import uuid

import pytest
from hypothesis import Phase, given, settings

import schemathesis
from schemathesis.contrib.openapi import formats


@pytest.fixture
def uuid_format():
    formats.uuid.install()
    yield
    formats.uuid.uninstall()


@pytest.mark.usefixtures("uuid_format")
@pytest.mark.hypothesis_nested
def test_generates_uuid(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/data/{key}/": {
                "get": {
                    "parameters": [
                        {
                            "name": "key",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.from_dict(schema)

    @given(case=schema["/data/{key}/"]["GET"].as_strategy())
    @settings(max_examples=3, phases=[Phase.generate], deadline=None)
    def test(case):
        value = case.path_parameters["key"]
        try:
            uuid.UUID(value)
        except ValueError:
            pytest.fail(f"UUID was expected, got: {value}")

    test()
