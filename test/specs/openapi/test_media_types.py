from pathlib import Path

import pytest
import yaml
from flask import jsonify
from hypothesis import strategies as st

import schemathesis
from schemathesis.core.file_samples import file_extension
from schemathesis.core.media_types import MEDIA_TYPE_STRATEGIES
from schemathesis.transport.asgi import ASGI_TRANSPORT
from schemathesis.transport.requests import REQUESTS_TRANSPORT
from schemathesis.transport.wsgi import WSGI_TRANSPORT
from test.utils import run_given

HERE = Path(__file__).absolute().parent


SAMPLE_PDF = (HERE / "blank.pdf").read_bytes()
PDFS = st.sampled_from([SAMPLE_PDF])
MEDIA_TYPE = "application/pdf"
ALIAS = "application/x-pdf"


def test_pdf_generation(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/pdf": {
                "post": {
                    "requestBody": {
                        "content": {
                            MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}},
                            ALIAS: {"schema": {"type": "string", "format": "binary"}},
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    schemathesis.openapi.media_type(MEDIA_TYPE, PDFS, aliases=[ALIAS])

    strategy = schema["/pdf"]["post"].as_strategy()

    @run_given(strategy, max_examples=100)
    def test(case):
        assert case.body == SAMPLE_PDF
        assert case.media_type in (MEDIA_TYPE, ALIAS)
        for transport in (REQUESTS_TRANSPORT, WSGI_TRANSPORT):
            assert transport.serialize_case(case)["data"] == SAMPLE_PDF


def test_wildcard_media_type_uses_matching_custom_strategy(ctx):
    payloads = {"model/stl": b"STL", "model/gltf-binary": b"GLTF"}
    for media_type, payload in payloads.items():
        schemathesis.openapi.media_type(media_type, st.just(payload))

    operation = ctx.openapi.load_operation(
        "POST /image",
        {
            "requestBody": {
                "required": True,
                "content": {"model/*": {"schema": {"type": "string", "format": "binary"}}},
            },
            "responses": {"204": {"description": "OK"}},
        },
    )
    seen = set()

    @run_given(case=operation.as_strategy(), max_examples=10)
    def test(case):
        assert case.body == payloads[case.media_type]
        seen.add(case.media_type)

    assert seen == set(payloads)


IMAGE_EXTENSIONS = {"image/gif": "gif", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


@pytest.mark.parametrize(
    ("declared", "expected"),
    [("image/png", {"image/png"}), ("image/jpeg", {"image/jpeg"}), ("image/*", set(IMAGE_EXTENSIONS))],
    ids=["png", "jpeg", "wildcard"],
)
def test_built_in_image_media_types_send_matching_files(ctx, declared, expected):
    operation = ctx.openapi.load_operation(
        "POST /image",
        {
            "requestBody": {
                "required": True,
                "content": {declared: {"schema": {"type": "string", "format": "binary"}}},
            },
            "responses": {"204": {"description": "OK"}},
        },
    )
    seen = set()

    @run_given(case=operation.as_strategy(), max_examples=30)
    def test(case):
        for transport in (REQUESTS_TRANSPORT, ASGI_TRANSPORT, WSGI_TRANSPORT):
            data = transport.serialize_case(case)["data"]
            assert file_extension(data) == IMAGE_EXTENSIONS[case.media_type], f"{case.media_type}: {data[:8]!r}"
        seen.add(case.media_type)

    assert seen == expected


def test_explicit_example_with_custom_media_type(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/csv": {
                "post": {
                    "requestBody": {
                        "content": {
                            "text/csv": {
                                "schema": {"type": "array", "items": {"type": "object"}},
                                "example": [{"a": 1, "b": 2, "c": 3}, {"a": 3, "b": 4, "c": 5}],
                            },
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    @app.route("/csv", methods=["POST"])
    def csv_handler():
        return jsonify({}), 200

    schemathesis.openapi.media_type("text/csv", st.sampled_from([b"a,b,c\n2,3,4"]))
    assert cli.run_openapi_app(app, "--mode=positive") == snapshot_cli


def test_malformed_registered_media_type_is_skipped(ctx):
    # Register a malformed media type (no slash, so it can't be parsed)
    MEDIA_TYPE_STRATEGIES["invalid"] = st.binary()

    # Create schema with valid content type that would trigger wildcard search
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"type": "object"}},
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    # Should not crash when encountering the malformed registered type
    strategy = schema["/data"]["post"].as_strategy()

    @run_given(strategy, max_examples=100)
    def test(case):
        assert case.media_type == "application/json"


# Pure-Python PyYAML closes top-level plain scalars with an explicit document end marker.
YAML_SCALAR_END = "" if yaml.__with_libyaml__ else "...\n"


@pytest.mark.parametrize(
    ("media_type", "body", "expected"),
    [
        ("application/yaml", "hello", f"hello\n{YAML_SCALAR_END}"),
        ("application/yaml", 42, f"42\n{YAML_SCALAR_END}"),
        ("application/xml", b"<a>1</a>", b"<a>1</a>"),
    ],
    ids=["yaml-string", "yaml-integer", "xml-bytes"],
)
def test_transport_data_for_scalar_yaml_and_bytes_xml_bodies(ctx, media_type, body, expected):
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {
                "content": {
                    "application/yaml": {"schema": {"type": "string"}},
                    "application/xml": {"schema": {"type": "string"}},
                }
            },
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(body=body, media_type=media_type)
    assert case.as_transport_kwargs(base_url="http://127.0.0.1")["data"] == expected


def test_jose_jwe_generation(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/jwe": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/jose+jwe": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"payload": {"type": "integer", "enum": [42]}},
                                    "required": ["payload"],
                                    "additionalProperties": False,
                                }
                            },
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    strategy = schema["/jwe"]["post"].as_strategy()

    @run_given(strategy, max_examples=100)
    def test(case):
        assert case.media_type == "application/jose+jwe"
        assert case.body == {"payload": 42}
        assert REQUESTS_TRANSPORT.serialize_case(case)["json"] == {"payload": 42}


def test_multipart_encoding_multiple_content_types(ctx):
    PNG_DATA = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
    JPEG_DATA = b"\xff\xd8\xff\xe0" + b"\x00" * 10
    schemathesis.openapi.media_type("image/png", st.just(PNG_DATA))
    schemathesis.openapi.media_type("image/jpeg", st.just(JPEG_DATA))

    operation = ctx.openapi.load_operation(
        "POST /upload",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "properties": {"image": {"type": "string", "format": "binary"}},
                            "required": ["image"],
                        },
                        "encoding": {"image": {"contentType": "image/png, image/jpeg"}},
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )

    strategy = operation.as_strategy()

    content_types_seen = set()

    @run_given(strategy, max_examples=100)
    def test(case):
        if isinstance(case.body, dict):
            files, _ = case.operation.prepare_multipart(case.body, case.media_type, case.multipart_content_types)

            if files:
                for file_tuple in files:
                    name = file_tuple[0]
                    if (
                        name == "image"
                        and len(file_tuple) > 1
                        and isinstance(file_tuple[1], tuple)
                        and len(file_tuple[1]) == 3
                    ):
                        _, _, content_type = file_tuple[1]
                        assert content_type in ["image/png", "image/jpeg"], (
                            f"Got invalid content type: {content_type} (should be 'image/png' or 'image/jpeg', "
                            f"not the literal 'image/png, image/jpeg')"
                        )
                        content_types_seen.add(content_type)

    assert len(content_types_seen) == 2, (
        f"Expected both content types to be selected, but only saw: {content_types_seen}"
    )


def test_multipart_encoding_array_content_type_with_custom_strategy(ctx):
    pdf_data = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"
    schemathesis.openapi.media_type("application/pdf", st.just(pdf_data))

    operation = ctx.openapi.load_operation(
        "POST /upload",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "properties": {"attachment": {"type": "string", "format": "binary"}},
                            "required": ["attachment"],
                        },
                        "encoding": {"attachment": {"contentType": ["application/pdf"]}},
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )

    strategy = operation.as_strategy()

    @run_given(strategy, max_examples=100)
    def test(case):
        assert isinstance(case.body, dict)
        assert case.body["attachment"] == pdf_data
        # Content type should match the value from the encoding array, not be omitted
        assert case.multipart_content_types["attachment"] == "application/pdf"


def test_custom_media_type_strategy_in_coverage_phase(ctx, testdir):
    # Regression test for GH-3345
    api = ctx.openapi.apps.success()
    testdir.make_test(
        f"""
schemathesis.openapi.media_type("application/pdf", st.just(b"%PDF-1.4"))
schema.config.update(base_url="{api.base_url}/api")
schema.config.phases.examples.enabled = False
schema.config.phases.fuzzing.enabled = False

@schema.include(path_regex="upload").parametrize()
def test(case):
    # Coverage also omits the required body entirely, which carries no payload to check.
    assert case.body is NOT_SET or case.body == b"%PDF-1.4", f"Expected PDF bytes, got: {{case.body!r}}"
""",
        paths={
            "/upload": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/pdf": {"schema": {"type": "string", "format": "binary"}},
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_multipart_encoding_with_custom_strategy_fuzzing_phase(ctx):
    schemathesis.openapi.media_type("application/pdf", st.just(b"%PDF-1.4"))

    operation = ctx.openapi.load_operation(
        "POST /upload",
        {
            "requestBody": {
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "properties": {"file": {"type": "string", "format": "binary"}},
                            "required": ["file"],
                        },
                        "encoding": {"file": {"contentType": "application/pdf"}},
                    },
                },
                "required": True,
            },
            "responses": {"200": {"description": "OK"}},
        },
    )

    strategy = operation.as_strategy()

    @run_given(strategy, max_examples=100)
    def test(case):
        if case.body is not None and isinstance(case.body, dict) and "file" in case.body:
            assert case.body["file"] == b"%PDF-1.4"


def test_multipart_encoding_with_custom_strategy_parametrize(ctx, testdir):
    # Regression test for GH-3345 - positive mode cases should have consistent PDF bytes
    api = ctx.openapi.apps.success()
    testdir.make_test(
        f"""
schemathesis.openapi.media_type("application/pdf", st.just(b"%PDF-1.4"))
schema.config.update(base_url="{api.base_url}/api")

@schema.include(path_regex="upload").parametrize()
def test(case):
    # Negative mode generates intentionally invalid data
    if case.meta.generation.mode == GenerationMode.POSITIVE:
        if case.body is not None and isinstance(case.body, dict) and "file" in case.body:
            assert case.body["file"] == b"%PDF-1.4", f"Expected PDF bytes, got: {{case.body['file']!r}}"
""",
        paths={
            "/upload": {
                "post": {
                    "parameters": [{"name": "tag", "in": "query", "required": False, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"file": {"type": "string", "format": "binary"}},
                                    "required": ["file"],
                                },
                                "encoding": {"file": {"contentType": "application/pdf"}},
                            },
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_multipart_encoding_required_body_parameter_coverage(ctx, testdir):
    # Regression test for GH-3345 - parameter coverage should still be generated
    api = ctx.openapi.apps.success()
    testdir.make_test(
        f"""
schemathesis.openapi.media_type("application/pdf", st.just(b"%PDF-1.4"))
schema.config.update(base_url="{api.base_url}/api")
schema.config.phases.fuzzing.enabled = False

param_values_seen = set()

@schema.include(path_regex="upload").parametrize()
def test(case):
    if case.query:
        param_values_seen.add(str(case.query.get("count")))

def test_parameter_coverage_generated():
    assert len(param_values_seen) > 0, f"No parameter coverage generated. Values seen: {{param_values_seen}}"
""",
        paths={
            "/upload": {
                "post": {
                    "parameters": [
                        {
                            "name": "count",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 1, "maximum": 10},
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"file": {"type": "string", "format": "binary"}},
                                    "required": ["file"],
                                },
                                "encoding": {"file": {"contentType": "application/pdf"}},
                            },
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=2)
