import warnings

import pytest


def pytest_configure(config):
    warnings.filterwarnings("ignore", category=pytest.PytestDeprecationWarning)
