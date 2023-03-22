import threading

import pytest
from pyrate_limiter import BucketFullException, RequestRate

import schemathesis.graphql


@pytest.mark.parametrize(
    "loader, fixture",
    (
        (schemathesis.graphql.from_url, "graphql_url"),
        (schemathesis.openapi.from_uri, "openapi3_schema_url"),
    ),
)
@pytest.mark.operations("success")
@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_maximum_requests(request, loader, fixture, mocker):
    mocker.patch(
        "pyrate_limiter.limit_context_decorator.LimitContextDecorator.delay_or_reraise",
        side_effect=BucketFullException("41", RequestRate(5, 3600), 0.0),
    )
    url = request.getfixturevalue(fixture)
    schema = loader(url, rate_limit="5/h")
    counter = 0

    def run():
        nonlocal counter
        while True:
            for operation in schema.get_all_operations():
                operation = operation.ok()
                if fixture == "graphql_url":
                    case = operation.make_case(body={})
                else:
                    case = operation.make_case()
                try:
                    case.call()
                except BucketFullException:
                    return
                counter += 1

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    assert counter == 5
