from __future__ import annotations

import warnings

import pytest

from test.apps.openapi.schema import OpenAPIVersion


def pytest_collection_modifyitems(session, config, items):
    """Add the `hypothesis_nested` marker to tests that depend on the `hypothesis_max_examples` fixture.

    During scheduled test runs on CI, we select such tests and run them with a higher number of examples.
    """
    for item in items:
        if isinstance(item, pytest.Function) and "hypothesis_max_examples" in item.fixturenames:
            item.add_marker("hypothesis_nested")


def pytest_generate_tests(metafunc):
    # A more ergonomic way to limit test parametrization to the specific Open API versions:
    #
    #     @pytest.mark.openapi_version("2.0")
    #
    #  or:
    #
    #     @pytest.mark.openapi_version("2.0", "3.0")
    if "openapi_version" in metafunc.fixturenames:
        marker = metafunc.definition.get_closest_marker("openapi_version")
        if marker is not None:
            variants = [OpenAPIVersion(variant) if isinstance(variant, str) else variant for variant in marker.args]
        else:
            variants = [OpenAPIVersion("2.0"), OpenAPIVersion("3.0")]
        metafunc.parametrize("openapi_version", variants)


def pytest_configure(config):
    config.addinivalue_line("markers", "operations(*names): Add only specified API operations to the test application.")
    config.addinivalue_line("markers", "snapshot(**kwargs): Configure snapshot tests.")
    config.addinivalue_line("markers", "snapshot_suffix(suffix): Append a suffix to the snapshot file name.")
    config.addinivalue_line("markers", "hypothesis_nested: Mark tests with nested Hypothesis tests.")
    config.addinivalue_line(
        "markers",
        "openapi_version(*versions): Restrict test parametrization only to the specified Open API version(s).",
    )
    warnings.filterwarnings("ignore", category=pytest.PytestDeprecationWarning)
