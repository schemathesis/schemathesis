import pathlib
from typing import NoReturn

import hypothesis
import pytest
from hypothesis import HealthCheck, Phase, Verbosity

import schemathesis
from schemathesis.exceptions import SchemaError
from schemathesis.constants import RECURSIVE_REFERENCE_ERROR_MESSAGE
from schemathesis.runner import events, from_schema
from schemathesis.runner.serialization import SerializedError
from schemathesis.specs.openapi import loaders

CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
CATALOG_DIR = CURRENT_DIR / "openapi-directory/APIs/"
schemathesis.experimental.OPEN_API_3_1.enable()


def get_id(path):
    return str(path).replace(f"{CATALOG_DIR}/", "")


def pytest_generate_tests(metafunc):
    allowed_schemas = (path for path in walk(CATALOG_DIR) if path.name in ("swagger.yaml", "openapi.yaml"))
    metafunc.parametrize("schema_path", allowed_schemas, ids=get_id)


def walk(path: pathlib.Path):
    # It is a bit faster than `glob`
    if path.is_dir():
        for item in path.iterdir():
            yield from walk(item)
    else:
        yield path


SLOW = {
    "stripe.com/2020-08-27/openapi.yaml",
    "azure.com/network-applicationGateway/2018-08-01/swagger.yaml",
    "azure.com/network-applicationGateway/2019-06-01/swagger.yaml",
    "azure.com/network-applicationGateway/2017-11-01/swagger.yaml",
    "azure.com/network-applicationGateway/2019-02-01/swagger.yaml",
    "azure.com/network-applicationGateway/2017-10-01/swagger.yaml",
    "azure.com/network-applicationGateway/2019-07-01/swagger.yaml",
    "azure.com/network-applicationGateway/2018-12-01/swagger.yaml",
    "azure.com/network-applicationGateway/2018-02-01/swagger.yaml",
    "azure.com/network-applicationGateway/2019-08-01/swagger.yaml",
    "azure.com/network-applicationGateway/2018-06-01/swagger.yaml",
    "azure.com/network-applicationGateway/2018-07-01/swagger.yaml",
    "azure.com/network-applicationGateway/2015-06-15/swagger.yaml",
    "azure.com/network-applicationGateway/2018-04-01/swagger.yaml",
    "azure.com/network-applicationGateway/2017-09-01/swagger.yaml",
    "azure.com/network-applicationGateway/2018-10-01/swagger.yaml",
    "azure.com/network-applicationGateway/2018-11-01/swagger.yaml",
    "azure.com/network-applicationGateway/2016-12-01/swagger.yaml",
    "azure.com/network-applicationGateway/2018-01-01/swagger.yaml",
    "azure.com/network-applicationGateway/2017-08-01/swagger.yaml",
    "azure.com/network-applicationGateway/2017-03-01/swagger.yaml",
    "azure.com/network-applicationGateway/2019-04-01/swagger.yaml",
    "azure.com/network-applicationGateway/2016-09-01/swagger.yaml",
    "azure.com/network-applicationGateway/2017-06-01/swagger.yaml",
    "azure.com/web-WebApps/2018-02-01/swagger.yaml",
    "azure.com/web-WebApps/2019-08-01/swagger.yaml",
    "azure.com/web-WebApps/2018-11-01/swagger.yaml",
    "azure.com/web-WebApps/2016-08-01/swagger.yaml",
    "azure.com/devtestlabs-DTL/2016-05-15/swagger.yaml",
    "azure.com/devtestlabs-DTL/2018-09-15/swagger.yaml",
    "amazonaws.com/resource-groups/2017-11-27/openapi.yaml",
    "amazonaws.com/ivs/2020-07-14/openapi.yaml",
    "presalytics.io/ooxml/0.1.0/openapi.yaml",
    "kubernetes.io/v1.10.0/swagger.yaml",
    "kubernetes.io/unversioned/swagger.yaml",
    "microsoft.com/graph/1.0.1/openapi.yaml",
    "wedpax.com/v1/swagger.yaml",
}


def test_runner(schema_path):
    schema_id = get_id(schema_path)
    if schema_id in SLOW:
        pytest.skip("Data generation is extremely slow for this schema")
    try:
        schema = loaders.from_path(schema_path, validate_schema=False)
    except SchemaError as exc:
        assert_invalid_schema(exc)
    runner = from_schema(
        schema,
        dry_run=True,
        count_operations=False,
        count_links=False,
        hypothesis_settings=hypothesis.settings(
            database=None,
            max_examples=1,
            suppress_health_check=list(HealthCheck),
            phases=[Phase.explicit, Phase.generate],
            verbosity=Verbosity.quiet,
        ),
    )
    for event in runner.execute():
        if isinstance(event, events.Interrupted):
            pytest.exit("Keyboard Interrupt")
        assert_event(schema_id, event)


def assert_invalid_schema(exc: SchemaError) -> NoReturn:
    error = str(exc.__cause__)
    if (
        "while scanning a block scalar" in error
        or "while parsing a block mapping" in error
        or "could not determine a constructor for the tag" in error
        or "unacceptable character" in error
    ):
        pytest.skip("Invalid schema")
    raise exc


def assert_event(schema_id: str, event: events.ExecutionEvent) -> None:
    if isinstance(event, events.AfterExecution):
        assert not event.result.has_failures, event.current_operation
        check_no_errors(schema_id, event)
    if isinstance(event, events.InternalError):
        raise AssertionError(f"Internal Error: {event.exception_with_traceback}")


def check_no_errors(schema_id: str, event: events.AfterExecution) -> None:
    for error in event.result.errors:
        if should_ignore_error(schema_id, error):
            continue
        raise AssertionError(f"{event.current_operation}: {error.exception_with_traceback}")


def should_ignore_error(schema_id: str, error: SerializedError) -> bool:
    if (
        schema_id == "launchdarkly.com/3.10.0/swagger.yaml" or schema_id == "launchdarkly.com/5.3.0/swagger.yaml"
    ) and "'<' not supported between instances" in error.exception:
        return True
    if (
        "is not a 'regex'" in error.exception
        or "Invalid regular expression" in error.exception
        or "Invalid `pattern` value: expected a string" in error.exception
    ):
        return True
    if "Failed to generate test cases for this API operation" in error.exception:
        return True
    if "Failed to generate test cases from examples for this API operation" in error.exception:
        return True
    if "FailedHealthCheck" in error.exception:
        return True
    if "Schemathesis can't serialize data" in error.exception:
        return True
    if "Malformed media type" in error.exception:
        return True
    if "Unresolvable JSON pointer in the schema" in error.exception:
        return True
    if RECURSIVE_REFERENCE_ERROR_MESSAGE in error.exception:
        return True
    return False
