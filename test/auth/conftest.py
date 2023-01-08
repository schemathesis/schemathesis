import pytest

import schemathesis


@pytest.fixture(autouse=True)
def unregister_global():
    yield
    schemathesis.auths.unregister()
