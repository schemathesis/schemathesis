import gc

import pytest


@pytest.fixture(scope="session", autouse=True)
def _freeze_gc() -> None:
    gc.collect()
    gc.freeze()


@pytest.fixture(autouse=True)
def _disable_gc():
    gc.disable()
    try:
        yield
    finally:
        gc.enable()
