import pytest

from schemathesis.transports.responses import copy_response


@pytest.mark.parametrize("factory_type", ("httpx", "requests", "werkzeug"))
def test_copy_response(response_factory, factory_type):
    response = getattr(response_factory, factory_type)()
    copy = copy_response(response)
    assert response.status_code == copy.status_code
    assert response.headers == copy.headers
    if factory_type == "werkzeug":
        assert response.get_data() == copy.get_data()
    else:
        assert response.content == copy.content
    assert response.request.url == copy.request.url
    assert response.request.headers == copy.request.headers
    if factory_type == "httpx":
        assert response.request.content == copy.request.content
    else:
        assert response.request.body == copy.request.body
