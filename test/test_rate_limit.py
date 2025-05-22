import threading

import pytest
from pyrate_limiter import BucketFullException, Duration, Rate, RateItem

import schemathesis.graphql
from schemathesis.core.rate_limit import _get_max_delay


@pytest.mark.parametrize(
    ("loader", "fixture"),
    [
        (schemathesis.graphql.from_url, "graphql_url"),
        (schemathesis.openapi.from_url, "openapi3_schema_url"),
    ],
)
@pytest.mark.operations("success")
@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_maximum_requests(request, loader, fixture, mocker):
    rate_item = RateItem("test_item", timestamp=None)
    side_effect = BucketFullException(rate_item, Rate(5, 3600))
    mocker.patch("pyrate_limiter.limiter.Limiter.delay_or_raise", side_effect=side_effect)
    url = request.getfixturevalue(fixture)
    schema = loader(url)
    schema.config.update(rate_limit="5/h")
    counter = 0

    def run():
        nonlocal counter
        while True:
            for operation in schema.get_all_operations():
                operation = operation.ok()
                if fixture == "graphql_url":
                    case = operation.Case(body={})
                else:
                    case = operation.Case()
                try:
                    case.call()
                except BucketFullException:
                    return
                counter += 1

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    assert counter == 5


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        (Duration.SECOND, 1100),
        (Duration.MINUTE, 60100),
        (Duration.HOUR, 3600100),
        (Duration.DAY, 86400100),
    ],
)
def test_get_max_delay(unit, expected):
    assert _get_max_delay(1, unit) == expected
