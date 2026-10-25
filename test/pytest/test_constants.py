import pytest

pytestmark = pytest.mark.usefixtures("clean_constants_registry")


@pytest.mark.parametrize(
    "schema_definition",
    [
        "schema = schemathesis.openapi.from_dict(raw_schema)",
        """
@pytest.fixture
def api_schema():
    return schemathesis.openapi.from_dict(raw_schema)

schema = schemathesis.pytest.from_fixture("api_schema")
""",
    ],
    ids=["eager", "lazy"],
)
def test_constants_extraction_warning_in_pytest_mode(testdir, schema_definition):
    testdir.makepyfile(
        """
import pytest
import schemathesis
from hypothesis import settings, Phase

raw_schema = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {"/x": {"get": {"responses": {"200": {"description": "OK"}}}}},
}

@schemathesis.python.constants
def broken_source():
    raise RuntimeError("boom in source")
"""
        + schema_definition
        + """

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    pass
"""
    )
    result = testdir.runpytest("-W", "always")
    result.stdout.re_match_lines([r".*broken_source.*"])


def test_constants_extraction_warning_for_loaded_app(testdir):
    # A loaded WSGI app is introspected directly, a different path than a URL/dict-loaded schema.
    testdir.makepyfile(
        """
import pytest
import schemathesis
from flask import Flask, jsonify
from hypothesis import settings, Phase

app = Flask(__name__)
SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {"/x": {"get": {"responses": {"200": {"description": "OK"}}}}},
}

@app.route("/openapi.json")
def spec():
    return jsonify(SPEC)

@app.route("/x")
def x():
    return jsonify({})

@schemathesis.python.constants
def broken_source():
    raise RuntimeError("boom in source")

schema = schemathesis.openapi.from_wsgi("/openapi.json", app=app)

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    pass
"""
    )
    result = testdir.runpytest("-W", "always")
    result.stdout.re_match_lines([r".*broken_source.*"])


def test_examples_phase_does_not_draw_application_constants(testdir):
    testdir.makepyfile(
        app="""
from flask import Flask, jsonify

EXAMPLES = {f"e{i}": {"value": f"v{i}"} for i in range(20)}
SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/u": {
            "get": {
                "parameters": [
                    {"name": "a", "in": "query", "required": True, "schema": {"type": "string"}, "examples": EXAMPLES},
                    {"name": "code", "in": "query", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}

app = Flask(__name__)

@app.route("/openapi.json")
def spec():
    return jsonify(SPEC)
""",
        test_examples="""
import schemathesis
from hypothesis import settings, Phase
from schemathesis.generation.meta import TestPhase
from app import app

schema = schemathesis.openapi.from_wsgi("/openapi.json", app=app)

@schema.parametrize()
@settings(phases=[Phase.explicit])
def test_api(case):
    if case.meta.phase.name == TestPhase.EXAMPLES:
        assert case.meta.constants_draws == ()
""",
    )
    result = testdir.runpytest("test_examples.py")
    result.assert_outcomes(passed=1)


def test_examples_phase_fill_missing_does_not_draw_application_constants(testdir):
    testdir.makepyfile(
        app="""
from flask import Flask, jsonify

SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/u": {
            "get": {
                "parameters": [{"name": "code", "in": "query", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}

app = Flask(__name__)

@app.route("/openapi.json")
def spec():
    return jsonify(SPEC)
""",
        test_examples="""
import schemathesis
from hypothesis import settings, Phase
from app import app

schema = schemathesis.openapi.from_wsgi("/openapi.json", app=app)
schema.config.phases.examples.fill_missing = True

@schema.parametrize()
@settings(phases=[Phase.explicit])
def test_api(case):
    assert case.meta.constants_draws == ()
""",
    )
    result = testdir.runpytest("test_examples.py")
    result.assert_outcomes(passed=1)
