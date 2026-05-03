import threading

import pytest
from pyrate_limiter import Duration

import schemathesis.graphql
from schemathesis.core.rate_limit import parse_units


def _graphql_url(ctx):
    return ctx.graphql.apps.books().schema_url


def _openapi_url(ctx):
    return ctx.openapi.apps.success().schema_url


@pytest.mark.parametrize(
    ("loader", "make_url", "kind"),
    [
        (schemathesis.graphql.from_url, _graphql_url, "graphql"),
        (schemathesis.openapi.from_url, _openapi_url, "openapi"),
    ],
)
@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_maximum_requests(ctx, loader, make_url, kind):
    schema = loader(make_url(ctx))
    schema.config.update(rate_limit="5/h")
    counter = 0

    def run():
        nonlocal counter
        for _ in range(5):
            for operation in schema.get_all_operations():
                operation = operation.ok()
                if kind == "graphql":
                    case = operation.Case(body={})
                else:
                    case = operation.Case()
                case.call()
                counter += 1

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=15)
    assert counter == 5


@pytest.mark.parametrize(
    ("rate_str", "expected_limit", "expected_interval"),
    [
        ("1/s", 1, Duration.SECOND),
        ("100/m", 100, Duration.MINUTE),
        ("1000/h", 1000, Duration.HOUR),
    ],
)
def test_parse_units(rate_str, expected_limit, expected_interval):
    limit, interval = parse_units(rate_str)
    assert limit == expected_limit
    assert interval == expected_interval
