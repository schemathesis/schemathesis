from __future__ import annotations

import warnings

import pytest

IGNORED_WARNING_STRICT_FILTERS = (
    # Keep globally ignored teardown noise ignored when a test opts into `filterwarnings("error")`.
    "ignore:Unclosed <MemoryObject.*:ResourceWarning",
    "ignore:.*Unclosed <MemoryObject.*:pytest.PytestUnraisableExceptionWarning",
    "ignore:Exception ignored while finalizing file <urllib3.response.HTTPResponse.*:pytest.PytestUnraisableExceptionWarning",
    "ignore:Exception ignored while finalizing file <http.client.HTTPResponse.*:pytest.PytestUnraisableExceptionWarning",
    "ignore:Exception ignored while calling deallocator <function BaseEventLoop.__del__.*:pytest.PytestUnraisableExceptionWarning",
    "ignore:Exception ignored while calling deallocator <function MemoryObject.*.__del__.*:pytest.PytestUnraisableExceptionWarning",
    "ignore:Exception ignored in.*<function MemoryObject.*.__del__.*:pytest.PytestUnraisableExceptionWarning",
    "ignore:Exception ignored in.*<socket.socket.*:pytest.PytestUnraisableExceptionWarning",
    "ignore:Exception ignored while finalizing socket <socket.socket.*:pytest.PytestUnraisableExceptionWarning",
    "ignore:unclosed <socket.socket.*:ResourceWarning",
)


def pytest_collection_modifyitems(session, config, items):
    # During scheduled CI runs we select hypothesis_nested-marked tests and bump max_examples.
    for item in items:
        if isinstance(item, pytest.Function) and "hypothesis_max_examples" in item.fixturenames:
            item.add_marker("hypothesis_nested")
        if any(
            arg.startswith("error")
            for mark in item.iter_markers(name="filterwarnings")
            for arg in mark.args
            if isinstance(arg, str)
        ):
            for warning_filter in IGNORED_WARNING_STRICT_FILTERS:
                item.add_marker(pytest.mark.filterwarnings(warning_filter))


def pytest_configure(config):
    config.addinivalue_line("markers", "snapshot(**kwargs): Configure snapshot tests.")
    config.addinivalue_line("markers", "snapshot_suffix(suffix): Append a suffix to the snapshot file name.")
    config.addinivalue_line("markers", "hypothesis_nested: Mark tests with nested Hypothesis tests.")
    warnings.filterwarnings("ignore", category=pytest.PytestDeprecationWarning)
