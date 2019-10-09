import hypothesis.strategies as st
import pytest

from schemathesis.models import Case, Endpoint


def test_contains(swagger_20):
    assert "/v1/users" in swagger_20


def test_getitem(swagger_20):
    assert "_endpoints" not in swagger_20.__dict__
    assert isinstance(swagger_20["/v1/users"], dict)
    # Check cached access
    assert "_endpoints" in swagger_20.__dict__
    assert isinstance(swagger_20["/v1/users"], dict)


def test_len(swagger_20):
    assert len(swagger_20) == 1


def test_iter(swagger_20):
    assert list(swagger_20) == ["/v1/users"]


def test_repr(swagger_20):
    assert str(swagger_20) == "SwaggerV20 for Sample API (1.0.0)"


def test_endpoint_access(swagger_20):
    assert isinstance(swagger_20["/v1/users"]["GET"], Endpoint)


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_as_strategy(swagger_20):
    strategy = swagger_20["/v1/users"]["GET"].as_strategy()
    assert isinstance(strategy, st.SearchStrategy)
    assert strategy.example() == Case(
        path="/v1/users", method="GET", path_parameters={}, headers={}, cookies={}, query={}, body={}, form_data={}
    )
