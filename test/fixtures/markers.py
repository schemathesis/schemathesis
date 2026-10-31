from __future__ import annotations

import pytest


def pytest_collection_modifyitems(session, config, items):
    # Keep globally ignored teardown noise ignored when a test opts into `filterwarnings("error")`.
    ignored_warnings = [line for line in config.getini("filterwarnings") if line.startswith("ignore:")]
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
            for warning_filter in ignored_warnings:
                item.add_marker(pytest.mark.filterwarnings(warning_filter))


def pytest_configure(config):
    config.addinivalue_line("markers", "snapshot(**kwargs): Configure snapshot tests.")
    config.addinivalue_line("markers", "snapshot_suffix(suffix): Append a suffix to the snapshot file name.")
    config.addinivalue_line("markers", "hypothesis_nested: Mark tests with nested Hypothesis tests.")
