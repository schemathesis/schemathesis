import pytest

import schemathesis
from schemathesis.constants import USER_AGENT

from .utils import SIMPLE_PATH


def test_path_loader(simple_schema):
    # Each loader method should read the specified schema correctly
    assert schemathesis.from_path(SIMPLE_PATH).raw_schema == simple_schema


def test_uri_loader(app_schema, app, schema_url):
    # Each loader method should read the specified schema correctly
    assert schemathesis.from_uri(schema_url).raw_schema == app_schema


def test_uri_loader_custom_kwargs(app, schema_url):
    # All custom kwargs are passed to `requests.get`
    schemathesis.from_uri(schema_url, verify=False, headers={"X-Test": "foo"})
    request = app["schema_requests"][0]
    assert request.headers["X-Test"] == "foo"
    assert request.headers["User-Agent"] == USER_AGENT


def test_base_url(base_url, schema_url):
    schema = schemathesis.from_uri(schema_url)
    assert schema.base_url == base_url


@pytest.mark.parametrize("url", ("http://example.com/", "http://example.com"))
def test_base_url_override(schema_url, url):
    schema = schemathesis.from_uri(schema_url, base_url=url)
    endpoint = next(schema.get_all_endpoints())
    assert endpoint.base_url == "http://example.com"


def test_backward_compatibility_path_loader(simple_schema):
    # The deprecated loaders should emit deprecation warnings
    message = r"^`Parametrizer.from_path` is deprecated, use `schemathesis.from_path` instead.\Z"
    with pytest.warns(DeprecationWarning, match=message):
        assert schemathesis.Parametrizer.from_path(SIMPLE_PATH).raw_schema == simple_schema


def test_backward_compatibility_uri_loader(schema_url, app_schema):
    # The deprecated loaders should emit deprecation warnings
    message = r"^`Parametrizer.from_uri` is deprecated, use `schemathesis.from_uri` instead.\Z"
    with pytest.warns(DeprecationWarning, match=message):
        assert schemathesis.Parametrizer.from_uri(schema_url).raw_schema == app_schema


def test_unsupported_type():
    with pytest.raises(ValueError, match="^Unsupported schema type$"):
        schemathesis.from_dict({})
