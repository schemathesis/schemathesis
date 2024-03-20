import platform
import uuid

import pytest
import requests.exceptions
from hypothesis import find

from schemathesis.service.extensions import apply, strategy_from_definitions
from schemathesis.service.models import StrategyDefinition, UnknownExtension, extension_from_dict
from schemathesis.specs.openapi.formats import STRING_FORMATS, unregister_string_format
from schemathesis.specs.openapi._hypothesis import Binary


@pytest.fixture
def string_formats():
    extension = extension_from_dict(
        {
            "type": "string_formats",
            "items": {
                # Simpler regex for faster search
                "_payment_card_regex": [{"name": "from_regex", "arguments": {"regex": "^[0-9]{2}$"}}],
                "_payment_card_regex_with_samples": [
                    {"name": "from_regex", "arguments": {"regex": "^[0-9]{2}$"}},
                    {"name": "sampled_from", "arguments": {"elements": ["1234-5678-1234-5678"]}},
                ],
                "_payment_card_samples": [
                    {"name": "sampled_from", "arguments": {"elements": ["1234-5678-1234-5679"]}},
                ],
                "_uuid": [{"name": "uuids", "transforms": [{"kind": "map", "name": "str"}]}],
            },
        }
    )
    yield extension
    for format in extension.formats:
        unregister_string_format(format)


def is_uuid(value):
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def test_string_formats_success(string_formats, openapi_30):
    assert str(string_formats.state) == "Not Applied"
    apply([string_formats], openapi_30)
    find(STRING_FORMATS["_payment_card_regex"], "42".__eq__)
    find(STRING_FORMATS["_payment_card_regex_with_samples"], "42".__eq__)
    find(STRING_FORMATS["_payment_card_regex_with_samples"], "1234-5678-1234-5678".__eq__)
    find(STRING_FORMATS["_payment_card_samples"], "1234-5678-1234-5679".__eq__)
    find(STRING_FORMATS["_uuid"], is_uuid)
    assert str(string_formats.state) == "Success"


@pytest.mark.parametrize(
    "definition, expected_type",
    (
        ([{"name": "uuids", "transforms": [{"kind": "map", "name": "str"}]}], str),
        ([{"name": "ip_addresses", "transforms": [{"kind": "map", "name": "str"}]}], str),
        ([{"name": "ip_addresses", "transforms": [{"kind": "map", "name": "str"}], "arguments": {"v": 6}}], str),
        ([{"name": "binary", "transforms": [{"kind": "map", "name": "base64_encode"}]}], Binary),
        ([{"name": "binary", "transforms": [{"kind": "map", "name": "urlsafe_base64_encode"}]}], Binary),
        (
            [
                {
                    "name": "integers",
                    "transforms": [{"kind": "map", "name": "str"}],
                    "arguments": {"min_value": 1, "max_value": 65535},
                }
            ],
            str,
        ),
        (
            [
                {
                    "name": "dates",
                    "transforms": [{"kind": "map", "name": "strftime", "arguments": {"format": "%Y-%m-%d"}}],
                }
            ],
            str,
        ),
        ([{"name": "timezone_keys"}], str),
        (
            [
                {
                    "name": "from_type",
                    "arguments": {"thing": "IPv4Network"},
                    "transforms": [{"kind": "map", "name": "str"}],
                }
            ],
            str,
        ),
        (
            [
                {"name": "timezone_keys"},
                {
                    "name": "dates",
                    "transforms": [{"kind": "map", "name": "strftime", "arguments": {"format": "%Y-%m-%d"}}],
                },
            ],
            str,
        ),
        ([{"name": "timezone_keys"}], str),
        ([{"name": "timezone_keys"}], str),
    ),
)
def test_strategy_from_definition(definition, expected_type):
    strategy = strategy_from_definitions([StrategyDefinition(**item) for item in definition])
    find(strategy.ok(), lambda x: isinstance(x, expected_type))


@pytest.mark.parametrize(
    "strategies, errors",
    (
        ([{"name": "from_regex", "arguments": {"regex": "[a-z"}}], ["Invalid regex: `[a-z`"]),
        ([{"name": "wrong"}], ["Unknown built-in strategy: `wrong`"]),
        (
            [{"name": "sampled_from", "arguments": {"elements": []}}],
            ["Invalid input for `sampled_from`: Cannot sample from a length-zero sequence"],
        ),
        (
            [
                {"name": "from_regex", "arguments": {"regex": r"\d"}},
                {"name": "sampled_from", "arguments": {"elements": []}},
            ],
            ["Invalid input for `sampled_from`: Cannot sample from a length-zero sequence"],
        ),
        # TODO: Handle this
        # ([{"unknown": 42}], ["Unsupported string format extension"]),
    ),
)
def test_invalid_string_format_extension(strategies, errors, openapi_30):
    extension = extension_from_dict({"type": "string_formats", "items": {"invalid": strategies}})
    apply([extension], openapi_30)
    assert str(extension.state) == "Error"
    assert extension.state.errors == errors
    assert "_invalid" not in STRING_FORMATS


def test_unknown_extension(openapi_30):
    extension = extension_from_dict({"type": "unknown", "custom": 42})
    assert isinstance(extension, UnknownExtension)
    apply([extension], openapi_30)
    assert str(extension.state) == "Not Applied"


@pytest.fixture
def cli_args(schema_url, service):
    return [
        schema_url,
        "my-api",
        f"--schemathesis-io-token={service.token}",
        f"--schemathesis-io-url={service.base_url}",
        "--report",
    ]


@pytest.mark.service(data={"detail": "Internal Server Error"}, status=500, method="POST", path="/cli/analysis/")
@pytest.mark.openapi_version("3.0")
def test_internal_server_error(cli_args, cli, service, snapshot_cli):
    assert cli.run(*cli_args) == snapshot_cli
    assert len(service.server.log) == 2
    service.assert_call(0, "/cli/analysis/", 500)


@pytest.mark.service(data={"detail": "Forbidden"}, status=403, method="POST", path="/cli/analysis/")
@pytest.mark.openapi_version("3.0")
def test_forbidden(cli_args, cli, service, snapshot_cli):
    assert cli.run(*cli_args) == snapshot_cli
    assert len(service.server.log) == 2
    service.assert_call(0, "/cli/analysis/", 403)


@pytest.mark.openapi_version("3.0")
@pytest.mark.parametrize("analyze_schema", [None])
def test_oversize_text(cli_args, cli, service, snapshot_cli, setup_server):
    payload = "JSON payload (20350625 bytes) is larger than allowed (limit: 10485760 bytes)"
    setup_server(
        lambda h: h.respond_with_data(payload, status=413),
        "POST",
        "/cli/analysis/",
    )
    assert cli.run(*cli_args) == snapshot_cli
    assert len(service.server.log) == 2
    service.assert_call(0, "/cli/analysis/", 413)


@pytest.mark.openapi_version("3.0")
@pytest.mark.skipif(platform.system() == "Windows", reason="Only verify on non-Windows platforms for simplicity")
def test_connection_error(mocker, cli_args, cli, snapshot_cli):
    try:
        requests.get("http://127.0.0.1:1", timeout=0.00001)
    except requests.exceptions.RequestException as exc:
        e = exc
    mocker.patch("schemathesis.service.client.ServiceClient.analyze_schema", side_effect=e)
    assert cli.run(*cli_args) == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.parametrize("analyze_schema", [None])
def test_invalid_payload(setup_server, cli_args, cli, snapshot_cli):
    # Analysis payload is invalid
    payload = "Json deserialize error: invalid type: integer `42`, expected a sequence at line 1 column 13"
    setup_server(
        lambda h: h.respond_with_data(payload, status=400),
        "POST",
        "/cli/analysis/",
    )
    assert cli.run(*cli_args) == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.extensions(
    {
        "type": "string_formats",
        "items": {
            "port": [
                {
                    "name": "integers",
                    "transforms": [{"kind": "map", "name": "str"}],
                    "arguments": {"min_value": 1, "max_value": 65535},
                }
            ],
        },
    }
)
def test_custom_format(cli, snapshot_cli, service, openapi3_base_url, empty_open_api_3_schema, testdir):
    empty_open_api_3_schema["paths"] = {
        "/success": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"port": {"type": "string", "format": "port"}},
                                "required": ["port"],
                                "additionalProperties": False,
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            },
        },
    }
    schema_file = testdir.make_openapi_schema_file(empty_open_api_3_schema)
    module = testdir.make_importable_pyfile(
        hook="""
import schemathesis

@schemathesis.check
def port_check(response, case):
    assert isinstance(case.body, dict), "Not a dict"
    assert "port" in case.body, "Missing key"
    assert 1 <= int(case.body["port"]) <= 65535, "Invalid port"
"""
    )
    assert (
        cli.main(
            "run",
            str(schema_file),
            "-c",
            "port_check",
            f"--base-url={openapi3_base_url}",
            f"--schemathesis-io-token={service.token}",
            f"--schemathesis-io-url={service.base_url}",
            "--hypothesis-max-examples=10",
            "--experimental=schema-analysis",
            hooks=module.purebasename,
        )
        == snapshot_cli
    )
