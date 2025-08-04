import re
import sys

import pytest
from flask import Flask, jsonify
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from schemathesis.specs.openapi.patterns import update_quantifier

SKIP_BEFORE_PY11 = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Possessive repeat is only available in Python 3.11+"
)


@pytest.mark.parametrize(
    ("pattern", "min_length", "max_length", "expected"),
    [
        # Single literal
        ("a", None, 3, "(a){1,3}"),
        ("a", 3, 3, "(a){3}"),
        ("a", 0, 3, "(a){1,3}"),
        ("}?", 1, None, "(}){1}"),
        # Simple quantifiers on a simple group
        (".*", None, 3, "(.){0,3}"),
        (".*", 0, 3, "(.){0,3}"),
        (".*", 1, None, "(.){1,}"),
        (".*", 1, 3, "(.){1,3}"),
        (".+", None, 3, "(.){1,3}"),
        (".+", 1, None, "(.){1,}"),
        (".+", 1, 3, "(.){1,3}"),
        (".+", 0, 3, "(.){1,3}"),
        (".?", 0, 3, "(.){0,1}"),
        (".*?", 0, 3, "(.){0,3}"),
        (".+?", 0, 3, "(.){1,3}"),
        # Complex quantifiers on a simple group
        (".{1,5}", None, 3, "(.){1,3}"),
        (".{0,3}", 1, None, "(.){1,3}"),
        (".{2,}", 1, 3, "(.){2,3}"),
        (".{1,5}?", None, 3, "(.){1,3}"),
        (".{0,3}?", 1, None, "(.){1,3}"),
        (".{2,}?", 1, 3, "(.){2,3}"),
        pytest.param(".{1,5}+", None, 3, "(.){1,3}", marks=SKIP_BEFORE_PY11),
        pytest.param(".{0,3}+", 1, None, "(.){1,3}", marks=SKIP_BEFORE_PY11),
        pytest.param(".{2,}+", 1, 3, "(.){2,3}", marks=SKIP_BEFORE_PY11),
        # Group without quantifier
        ("[a-z]", None, 5, "([a-z]){1,5}"),
        ("[a-z]", 3, None, "([a-z]){3,}"),
        ("[a-z]", 3, 5, "([a-z]){3,5}"),
        ("[a-z]", 1, 5, "([a-z]){1,5}"),
        ("a|b", 1, 5, "(a|b){1,5}"),
        # A more complex group with `*` quantifier
        ("[a-z]*", None, 5, "([a-z]){0,5}"),
        ("[a-z]*", 3, None, "([a-z]){3,}"),
        ("[a-z]*", 3, 5, "([a-z]){3,5}"),
        ("[a-z]*", 1, 5, "([a-z]){1,5}"),
        # With anchors
        ("^[a-z]*", None, 5, "^([a-z]){0,5}"),
        ("^[a-z]*", 3, 5, "^([a-z]){3,5}"),
        ("^[a-z]+", 0, 5, "^([a-z]){1,5}"),
        ("^[a-z]*$", None, 5, "^([a-z]){0,5}$"),
        ("^[a-z]*$", 3, 5, "^([a-z]){3,5}$"),
        ("^[a-z]+$", 0, 5, "^([a-z]){1,5}$"),
        ("^.+$", 0, 5, "^(.){1,5}$"),
        ("^.{0,1}$", 0, 5, "^(.){0,1}$"),
        ("^.$", 0, 5, "^(.){1}$"),
        ("[a-z]*$", None, 5, "([a-z]){0,5}$"),
        ("[a-z]*$", 3, 5, "([a-z]){3,5}$"),
        ("[a-z]+$", 0, 5, "([a-z]){1,5}$"),
        (r"\d*", 1, None, r"(\d){1,}"),
        (r"0\A", 1, None, r"(0){1,}\A"),
        # Noop
        ("abc*def*", 1, 3, "abc*def*"),
        ("[bc]*[de]*", 1, 3, "[bc]*[de]*"),
        ("[bc]3", 1, 3, "[bc]3"),
        ("b{30,35}", 1, 3, "b{30,35}"),
        ("b{1,3}", 10, None, "b{1,3}"),
        ("b", 0, 0, "b"),
        ("b$", None, None, "b$"),
        ("b$", 0, None, "b$"),
        ("}?", 0, None, "}?"),
        # Literal length is outside of the quantifiers range
        ("^0$", 2, 2, "^0$"),
        ("^0$", 2, None, "^0$"),
        ("^0$", 0, 0, "^0$"),
        # More complex patterns
        # Fixed parts with single quantifier
        ("^abc[0-9]*$", None, 5, "^abc([0-9]){0,2}$"),
        ("^-[a-z]{1,10}-$", None, 4, "^-([a-z]){1,2}-$"),
        # Multiple quantifiers
        (r"^[a-z]{2,4}-\d{4,15}$", 7, 7, r"^([a-z]){2}-(\d){4}$"),
        (r"^[a-z]{2,4}-\d{4,15}$", 20, 20, r"^([a-z]){4}-(\d){15}$"),
        # Complex patterns with multiple parts
        ("^[A-Z]{1,3}-[0-9]{2,4}-[a-z]{1,5}$", 8, 8, "^([A-Z]){1}-([0-9]){2}-([a-z]){3}$"),
        (r"^\w{2,4}:\d{3,5}:[A-F]{1,2}$", 10, 10, r"^(\w){2}:(\d){4}:([A-F]){2}$"),
        (r"^[a-zA-Z0-9]{2,4}-\d{4,15}$", 7, 7, r"^([a-zA-Z0-9]){2}-(\d){4}$"),
        (r"^[a-zA-Z0-9]{2,4}-\d{4,15}$", 8, 8, r"^([a-zA-Z0-9]){2}-(\d){5}$"),
        (r"^[a-zA-Z0-9]{2,4}-\d{4,15}$", 19, 19, r"^([a-zA-Z0-9]){3}-(\d){15}$"),
        (r"^([a-zA-Z0-9]){2,4}-(\d){4,15}$", 19, 19, r"^([a-zA-Z0-9]){3}-(\d){15}$"),
        (r"^[a-zA-Z0-9]{2,4}-\d{4,15}$", 50, 50, r"^[a-zA-Z0-9]{2,4}-\d{4,15}$"),
        (r"^abcd[a-zA-Z0-9]{2,4}$", 1, 5, r"^abcd[a-zA-Z0-9]{2,4}$"),
        (r"^abcd[a-zA-Z0-9]{2,4}$", 5, 1, r"^abcd[a-zA-Z0-9]{2,4}$"),
        (r"^abcd[a-zA-Z0-9]{2,4}$", 5, 5, r"^abcd[a-zA-Z0-9]{2,4}$"),
        (r"^abcd[a-zA-Z0-9]{2,4}$", None, None, r"^abcd[a-zA-Z0-9]{2,4}$"),
        (r"^abcd[a-zA-Z0-9]{2,4}$", 0, None, r"^abcd[a-zA-Z0-9]{2,4}$"),
        (r"^abcd[a-zA-Z0-9]{2,4}$", None, 5, r"^abcd[a-zA-Z0-9]{2,4}$"),
        (r"^abcd[a-zA-Z0-9]{2,4}$", 5, None, r"^abcd([a-zA-Z0-9]){2,4}$"),
        (r"^abcd[a-zA-Z0-9]{2,4}$", 5, 10, r"^abcd([a-zA-Z0-9]){2,4}$"),
        (r"^[a-zA-Z0-9]+([-a-zA-Z0-9]?[a-zA-Z0-9])*$", 5, 64, r"^([a-zA-Z0-9]){5,64}([-a-zA-Z0-9]?[a-zA-Z0-9]){0}$"),
        (r"^\+[0-9]{5,}$", 6, 6, r"^\+([0-9]){5}$"),
        (r"^abcd$", 50, 50, r"^abcd$"),
        # Edge cases
        ("^[a-z]*-[0-9]*$", 3, 3, "^([a-z]){0}-([0-9]){2}$"),
        (r"^[+][\s0-9()-]+$", 1, 20, r"^[+]([\s0-9()-]){1,19}$"),
        (r"^[\+][\s0-9()-]+$", 1, 20, r"^[\+]([\s0-9()-]){1,19}$"),
        # Multiple fixed parts
        ("^abc[0-9]{1,3}def[a-z]{2,5}ghi$", 12, 12, "^abc([0-9]){1}def([a-z]){2}ghi$"),
        # Others
        ("^(((?:DB|BR)[-a-zA-Z0-9_]+),?){1,}$", None, 6000, "^(((?:DB|BR)[-a-zA-Z0-9_]+),?){1,6000}$"),
        (r"^geo:\w*\*?$", 5, 200, r"^geo:(\w){1,196}(\*){0}$"),
        (r"^[\w\W]$", 1, 3, r"^(.){1}$"),
        (r"^[\w\W]+$", 1, 3, r"^(.){1,3}$"),
        (r"^[\w\W]*$", 1, 3, r"^(.){1,3}$"),
        (r"^[\w\W]?$", 1, 3, r"^(.){1}$"),
        (r"^[\w\W]{2,}$", 1, 3, r"^(.){2,3}$"),
        (r"^[\W\w]$", 1, 3, r"^(.){1}$"),
        (r"^[\W\w]+$", 1, 3, r"^(.){1,3}$"),
        (r"^[\W\w]*$", 1, 3, r"^(.){1,3}$"),
        (r"^[\W\w]?$", 1, 3, r"^(.){1}$"),
        (r"^[\W\w]{2,}$", 1, 3, r"^(.){2,3}$"),
        (r"^prefix[|]+(?:,prefix[|]+)*$", 4000, 4000, r"^prefix([|]){2}(?:,prefix[|]+){499}$"),
        (r"^bar\.spam\.[^,]+(?:,bar\.spam\.[^,]+)*$", 10, 10, r"^bar\.spam\.([^,]){1}(?:,bar\.spam\.[^,]+){0}$"),
        (r"^\008+()?$", None, 2, r"^\00(8){1}(){0}$"),
        (r"^\008+()?$", 2, None, r"^\00(8){1,}(){0}$"),
        (r"^000(000)?$", 4, 5, r"^000(000)?$"),
        ("(abc)+", 1, 10, "(abc){1,3}"),
        ("(hello){2,5}", None, 12, "(hello){2}"),
        ("(abcd)*", 3, 7, "(abcd){1}"),
        ("^()?$", 4, 5, "^()?$"),
    ],
)
def test_update_quantifier(pattern, min_length, max_length, expected):
    assert update_quantifier(pattern, min_length, max_length) == expected
    re.compile(expected)


def test_update_quantifier_invalid_pattern():
    assert update_quantifier("*", 1, 3) == "*"


@given(st.data())
@settings(suppress_health_check=list(HealthCheck))
def test_update_quantifier_random(data):
    # Generate a regex pattern
    pattern = data.draw(st.text(min_size=1).filter(is_valid_regex))

    # Generate optional length constraints
    min_length = data.draw(st.integers(min_value=0, max_value=100) | st.none())
    max_length = data.draw(st.integers(min_value=0, max_value=100) | st.none())

    # Ensure min_length <= max_length if both are present
    assume(
        max_length is None
        or min_length is None
        or min_length <= max_length
        and not (min_length is None and max_length is None)
    )

    # Apply length constraints
    modified_pattern = update_quantifier(pattern, min_length, max_length)

    assume(pattern != modified_pattern)

    # Ensure the modified pattern is a valid regex
    assert is_valid_regex(modified_pattern)

    # Generate a string matching the modified pattern
    generated = data.draw(st.from_regex(modified_pattern, fullmatch=True, alphabet=st.characters(codec=None)))

    # Assert that the generated string meets the length constraints
    if min_length is not None:
        assert len(generated) >= min_length, (
            f"Generated string '{generated}' is shorter than min_length {min_length}\nOriginal pattern: {pattern}\nModified pattern: {modified_pattern}"
        )
    if max_length is not None:
        assert len(generated) <= max_length, (
            f"Generated string '{generated}' is longer than max_length {max_length}.\nOriginal pattern: {pattern}\nModified pattern: {modified_pattern}"
        )
    assert re.search(pattern, generated), (
        f"Generated string '{generated}' does not match the pattern.\nOriginal pattern: {pattern}\nModified pattern: {modified_pattern}"
    )


def is_valid_regex(pattern: str) -> bool:
    try:
        re.compile(pattern)
        return True
    except re.error:
        return False


def test_response_schema_is_not_mutated(cli, app_runner, snapshot_cli):
    # See GH-2749
    raw_schema = {
        "openapi": "3.0.3",
        "info": {"title": "Container Image API", "version": "1.0.0"},
        "paths": {
            "/container": {
                "post": {
                    "summary": "Create a container image",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"container_image": {"$ref": "#/components/schemas/ContainerImage"}},
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Successful response",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "container_image": {"$ref": "#/components/schemas/ContainerImage"}
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "ContainerImage": {
                    "description": "A container image",
                    "type": "string",
                    "maxLength": 500,
                    "pattern": "^[a-z0-9]+((\\.|_|__|-+)[a-z0-9]+)*(\\/[a-z0-9]+((\\.|_|__|-+)[a-z0-9]+)*)*(:[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}|@sha256:[a-fA-F0-9]{64}){0,1}$",
                    "example": "renku/renkulab-py:3.10-0.18.1",
                }
            }
        },
    }

    app = Flask(__name__)

    @app.route("/openapi.json")
    def openapi_spec():
        return jsonify(raw_schema)

    @app.route("/container", methods=["POST"])
    def create_container():
        example_value = raw_schema["components"]["schemas"]["ContainerImage"]["example"]
        response_body = {"container_image": example_value}
        return jsonify(response_body), 200

    port = app_runner.run_flask_app(app)

    assert cli.run(f"http://127.0.0.1:{port}/openapi.json", "-call", "--phases=fuzzing", "-n 1") == snapshot_cli
