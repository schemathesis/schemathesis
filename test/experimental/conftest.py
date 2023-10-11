import pytest

from schemathesis.experimental import GLOBAL_EXPERIMENTS


@pytest.fixture(autouse=True)
def cleanup():
    yield
    GLOBAL_EXPERIMENTS.disable_all()
