from __future__ import annotations

import warnings

import pytest


def pytest_collection_modifyitems(session, config, items):
    # During scheduled CI runs we select hypothesis_nested-marked tests and bump max_examples.
    for item in items:
        if isinstance(item, pytest.Function) and "hypothesis_max_examples" in item.fixturenames:
            item.add_marker("hypothesis_nested")


def pytest_configure(config):
    config.addinivalue_line("markers", "snapshot(**kwargs): Configure snapshot tests.")
    config.addinivalue_line("markers", "snapshot_suffix(suffix): Append a suffix to the snapshot file name.")
    config.addinivalue_line("markers", "hypothesis_nested: Mark tests with nested Hypothesis tests.")
    warnings.filterwarnings("ignore", category=pytest.PytestDeprecationWarning)
