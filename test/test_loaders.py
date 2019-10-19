import pytest

import schemathesis

from .utils import SIMPLE_PATH


@pytest.mark.parametrize(
    "method, path", ((schemathesis.from_path, SIMPLE_PATH), (schemathesis.from_uri, f"file://{SIMPLE_PATH}"))
)
def test_loaders(simple_schema, method, path):
    # Each loader method should read the specified schema correctly
    assert method(path).raw_schema == simple_schema


def test_base_url(base_url, schema_url):
    schema = schemathesis.from_uri(schema_url)
    assert schema.base_url == base_url


@pytest.mark.parametrize("url", ("http://example.com/", "http://example.com"))
def test_base_url_override(schema_url, url):
    schema = schemathesis.from_uri(schema_url, base_url=url)
    endpoint = next(schema.get_all_endpoints())
    assert endpoint.base_url == "http://example.com"


@pytest.mark.parametrize(
    "method, path, message",
    (
        (
            schemathesis.Parametrizer.from_path,
            SIMPLE_PATH,
            r"^`Parametrizer.from_path` is deprecated, use `schemathesis.from_path` instead.\Z",
        ),
        (
            schemathesis.Parametrizer.from_uri,
            f"file://{SIMPLE_PATH}",
            r"^`Parametrizer.from_uri` is deprecated, use `schemathesis.from_uri` instead.\Z",
        ),
    ),
)
def test_backward_compatibility(simple_schema, method, path, message):
    # The deprecated loaders should emit deprecation warnings
    with pytest.warns(DeprecationWarning, match=message):
        assert method(path).raw_schema == simple_schema


def test_unsupported_type():
    with pytest.raises(ValueError, match="^Unsupported schema type$"):
        schemathesis.from_dict({})
