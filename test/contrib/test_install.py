import pytest

from schemathesis import contrib
from schemathesis.specs import openapi


@pytest.fixture
def contrib_cleanup():
    yield
    contrib.uninstall()


@pytest.mark.usefixtures("contrib_cleanup")
def test_installation():
    assert contrib.openapi.formats.uuid.FORMAT_NAME not in openapi._hypothesis.STRING_FORMATS
    contrib.install()
    assert contrib.openapi.formats.uuid.FORMAT_NAME in openapi._hypothesis.STRING_FORMATS
