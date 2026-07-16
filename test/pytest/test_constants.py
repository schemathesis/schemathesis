import pytest

from schemathesis.python._constants.registry import default_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    # `testdir.runpytest()` runs in-process, so a `@schemathesis.python.constants` in the generated
    # module registers on the global registry; clear it so it does not leak into later tests.
    default_registry().clear()
    yield
    default_registry().clear()


def test_constants_extraction_warning_in_pytest_mode(testdir):
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

schema = schemathesis.openapi.from_dict(raw_schema)

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    pass
"""
    )
    result = testdir.runpytest("-W", "always")
    result.stdout.re_match_lines([r".*broken_source.*"])


def test_constants_extraction_warning_in_lazy_pytest_mode(testdir):
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

@pytest.fixture
def api_schema():
    return schemathesis.openapi.from_dict(raw_schema)

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

@lazy_schema.parametrize()
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
