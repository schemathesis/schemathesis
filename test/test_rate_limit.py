import threading

import pytest
from pyrate_limiter import BucketFullException, Duration

import schemathesis.graphql
from schemathesis._dependency_versions import IS_PYRATE_LIMITER_ABOVE_3
from schemathesis._rate_limiter import Rate
from schemathesis.throttling import _get_max_delay


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
    if IS_PYRATE_LIMITER_ABOVE_3:
        from pyrate_limiter import RateItem

        target = "pyrate_limiter.limiter.Limiter.delay_or_raise"
        rate_item = RateItem("test_item", timestamp=None)
        side_effect = BucketFullException(rate_item, Rate(5, 3600))
    else:
        target = "pyrate_limiter.limit_context_decorator.LimitContextDecorator.delay_or_reraise"
        side_effect = BucketFullException("41", Rate(5, 3600), 0.0)
    mocker.patch(target, side_effect=side_effect)
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


@pytest.mark.parametrize(
    "unit, expected",
    (
        (Duration.SECOND, 1100),
        (Duration.MINUTE, 60100),
        (Duration.HOUR, 3600100),
        (Duration.DAY, 86400100),
    ),
)
def test_get_max_delay(unit, expected):
    assert _get_max_delay(1, unit) == expected
