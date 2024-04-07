import pytest
from hypothesis import given, settings


@pytest.fixture
def run_test():
    # Minimal smoke test to check whether `call_*` methods work successfully
    def inner(strategy):
        @given(case=strategy)
        @settings(max_examples=1, deadline=None)
        def test(case):
            response = case.call()
            assert response.status_code == 200

        test()

    return inner
