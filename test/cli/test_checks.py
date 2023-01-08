import pytest

import schemathesis
from schemathesis.cli import reset_checks


@pytest.fixture
def new_check():
    @schemathesis.check
    def check_function(response, case):
        pass

    yield check_function

    reset_checks()


def test_register_returns_a_value(new_check):
    # When a function is registered via the `schemathesis.check` decorator
    # Then this function should be available for further usage
    # See #721
    assert new_check is not None
