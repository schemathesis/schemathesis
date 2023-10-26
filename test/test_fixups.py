import pytest
from pytest import ExitCode

from schemathesis import fixups


def test_global_fixup(testdir, fast_api_schema):
    # When all fixups are enabled globally
    testdir.makepyfile(
        """
import schemathesis
from hypothesis import settings

schemathesis.fixups.install()
schema = schemathesis.from_dict({schema})

def teardown_module(module):
    schemathesis.fixups.uninstall()
    assert schemathesis.hooks.get_all_by_name("before_load_schema") == []

@schema.parametrize()
@settings(max_examples=1)
def test(case):
    assert 0 < case.query["value"] < 10
    """.format(schema=fast_api_schema),
    )
    # Then Fast API schemas that are not compliant should be processed
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize(
    "value, expected",
    (
        # No-op case
        ({"exclusiveMinimum": True, "minimum": 5}, {"exclusiveMinimum": True, "minimum": 5}),
        # Draft 7 to Draft 4
        ({"exclusiveMinimum": 5}, {"exclusiveMinimum": True, "minimum": 5}),
        ({"exclusiveMaximum": 5}, {"exclusiveMaximum": True, "maximum": 5}),
        # Nested cases
        ({"schema": {"exclusiveMaximum": 5}}, {"schema": {"exclusiveMaximum": True, "maximum": 5}}),
        ([{"schema": {"exclusiveMaximum": 5}}], [{"schema": {"exclusiveMaximum": True, "maximum": 5}}]),
    ),
)
def test_fastapi_schema_conversion(value, expected):
    fixups.fast_api.before_load_schema(None, value)
    assert value == expected


@pytest.fixture(params=["flask_app", "openapi_3_app"])
def app_args(request, testdir, operations):
    if request.param == "flask_app":
        module = testdir.make_importable_pyfile(
            location=f"""
            from test.apps.openapi._flask import create_app

            app = create_app({operations})
            app.config["prefix_with_bom"] = True
            """
        )
        return f"--app={module.purebasename}:app", "/schema.yaml"
    app = request.getfixturevalue(request.param)
    app["config"]["prefix_with_bom"] = True
    return (request.getfixturevalue("openapi3_schema_url"),)


@pytest.mark.operations("success")
def test_bom_json(app_args, cli):
    # When server responds with JSON that contains BOM
    # And the `utf8_bom` fixup is enabled
    result = cli.run(*app_args, "--fixups=utf8_bom", "--checks=response_schema_conformance")
    assert fixups.is_installed("utf8_bom")
    # Then the data should be properly decoded
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "Unexpected UTF-8 BOM (decode using utf-8-sig)" not in result.stdout
