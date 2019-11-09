import hypothesis.strategies as st
import pytest
from requests.structures import CaseInsensitiveDict

import schemathesis
from schemathesis.models import Case, Endpoint
from schemathesis.schemas import endpoints_to_dict


def test_contains(swagger_20):
    assert "/v1/users" in swagger_20


def test_getitem(simple_schema, mocker):
    swagger = schemathesis.from_dict(simple_schema)
    mocked = mocker.patch("schemathesis.schemas.endpoints_to_dict", wraps=endpoints_to_dict)
    assert "_endpoints" not in swagger.__dict__
    assert isinstance(swagger["/v1/users"], CaseInsensitiveDict)
    assert mocked.call_count == 1
    # Check cached access
    assert "_endpoints" in swagger.__dict__
    assert isinstance(swagger["/v1/users"], CaseInsensitiveDict)
    assert mocked.call_count == 1


def test_len(swagger_20):
    assert len(swagger_20) == 1


def test_iter(swagger_20):
    assert list(swagger_20) == ["/v1/users"]


def test_repr(swagger_20):
    assert str(swagger_20) == "SwaggerV20 for Sample API (1.0.0)"


@pytest.mark.parametrize("method", ("GET", "get"))
def test_endpoint_access(swagger_20, method):
    assert isinstance(swagger_20["/v1/users"][method], Endpoint)


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_as_strategy(swagger_20):
    strategy = swagger_20["/v1/users"]["GET"].as_strategy()
    assert isinstance(strategy, st.SearchStrategy)
    assert strategy.example() == Case(
        path="/v1/users", method="GET", path_parameters={}, headers={}, cookies={}, query={}, body=None, form_data={}
    )
