import pytest
from hypothesis import given, settings


def make_inner(get_response):
    # Minimal smoke test to check whether `call_*` methods work successfully
    def inner(strategy):
        @given(case=strategy)
        @settings(max_examples=1, deadline=None)
        def test(case):
            response = get_response(case)
            assert response.status_code == 200

        test()

    return inner


@pytest.fixture
def run_asgi_test():
    return make_inner(lambda c: c.call_asgi())


@pytest.fixture
def run_wsgi_test():
    return make_inner(lambda c: c.call_wsgi())
