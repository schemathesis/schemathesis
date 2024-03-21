import pathlib
from typing import NoReturn

import os
import hypothesis
import pytest
from flask import Flask
from jsonschema import RefResolutionError
from hypothesis import HealthCheck, Phase, Verbosity

import schemathesis
from schemathesis.checks import ALL_CHECKS
from schemathesis.extra._flask import run_server
from schemathesis.exceptions import SchemaError, CheckFailed, UsageError, format_exception
from schemathesis.constants import RECURSIVE_REFERENCE_ERROR_MESSAGE
from schemathesis.models import Status
from schemathesis.internal.result import Err
from schemathesis.runner import events, from_schema
from schemathesis.runner.serialization import SerializedError
from schemathesis.service.client import ServiceClient
from schemathesis.service.constants import URL_ENV_VAR, TOKEN_ENV_VAR
from schemathesis.service.models import SuccessState, AnalysisError
from schemathesis.specs.openapi import loaders

CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
CATALOG_DIR = CURRENT_DIR / "openapi-directory/APIs/"
schemathesis.experimental.OPEN_API_3_1.enable()
VERIFY_SCHEMA_ANALYSIS = os.getenv("VERIFY_SCHEMA_ANALYSIS", "false").lower() in ("true", "1")
SCHEMATHESIS_IO_URL = os.getenv(URL_ENV_VAR)
SCHEMATHESIS_IO_TOKEN = os.getenv(TOKEN_ENV_VAR)


app = Flask("test_app")


@app.route("/")
def default():
    return '{"success": true}'


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
    "amazonaws.com/workspaces-web/2020-07-08/openapi.yaml",
    "presalytics.io/ooxml/0.1.0/openapi.yaml",
    "kubernetes.io/v1.10.0/swagger.yaml",
    "kubernetes.io/unversioned/swagger.yaml",
    "microsoft.com/graph/1.0.1/openapi.yaml",
    "microsoft.com/graph-beta/1.0.1/openapi.yaml",
    "wedpax.com/v1/swagger.yaml",
    "stripe.com/2022-11-15/openapi.yaml",
    "xero.com/xero-payroll-au/2.9.4/openapi.yaml",
    "xero.com/xero_accounting/2.9.4/openapi.yaml",
    "portfoliooptimizer.io/1.0.9/openapi.yaml",
    "amazonaws.com/proton/2020-07-20/openapi.yaml",
    "bungie.net/2.18.0/openapi.yaml",
    "amazonaws.com/sagemaker-geospatial/2020-05-27/openapi.yaml",
}
KNOWN_ISSUES = {
    # Regex that includes surrogates which is incompatible with the default alphabet for regex in Hypothesis (UTF-8)
    ("amazonaws.com/cleanrooms/2022-02-17/openapi.yaml", "POST /collaborations"),
    ("amazonaws.com/cleanrooms/2022-02-17/openapi.yaml", "POST /configuredTables"),
}


@pytest.fixture(scope="session")
def app_port():
    return run_server(app)


def combined_check(response, case):
    case.get_code_to_reproduce()
    case.as_curl_command()
    for check in ALL_CHECKS:
        try:
            check(response, case)
        except CheckFailed:
            pass


def test_corpus(schema_path, app_port):
    schema_id = get_id(schema_path)
    if schema_id in SLOW:
        pytest.skip("Data generation is extremely slow for this schema")
    try:
        schema = loaders.from_path(schema_path, validate_schema=False, base_url=f"http://127.0.0.1:{app_port}/")
    except SchemaError as exc:
        assert_invalid_schema(exc)
    try:
        schema.as_state_machine()()
    except (RefResolutionError, UsageError, SchemaError):
        pass

    service_client = None
    if VERIFY_SCHEMA_ANALYSIS:
        assert SCHEMATHESIS_IO_URL, "SCHEMATHESIS_IO_URL is not set"
        assert SCHEMATHESIS_IO_TOKEN, "SCHEMATHESIS_IO_TOKEN is not set"
        service_client = ServiceClient(base_url=SCHEMATHESIS_IO_URL, token=SCHEMATHESIS_IO_TOKEN)
    runner = from_schema(
        schema,
        checks=(combined_check,),
        count_operations=False,
        count_links=False,
        hypothesis_settings=hypothesis.settings(
            deadline=None,
            database=None,
            max_examples=1,
            suppress_health_check=list(HealthCheck),
            phases=[Phase.explicit, Phase.generate],
            verbosity=Verbosity.quiet,
        ),
        service_client=service_client,
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
        failures = [check for check in event.result.checks if check.value == Status.failure]
        assert not failures, event.current_operation
        check_no_errors(schema_id, event)
        # Errors are checked above and unknown ones cause a test failure earlier
        assert event.status in (Status.success, Status.skip, Status.error)
    if isinstance(event, events.InternalError):
        raise AssertionError(f"Internal Error: {event.exception_with_traceback}")
    if VERIFY_SCHEMA_ANALYSIS and isinstance(event, events.AfterAnalysis):
        assert event.analysis is not None
        if isinstance(event.analysis, Err):
            traceback = format_exception(event.analysis.err(), True)
            raise AssertionError(f"Analysis failed: {traceback}")
        else:
            analysis = event.analysis.ok()
            assert not isinstance(analysis, AnalysisError)
            for extension in analysis.extensions:
                assert isinstance(extension.state, SuccessState), extension


def check_no_errors(schema_id: str, event: events.AfterExecution) -> None:
    if event.result.has_errors:
        assert event.result.errors, event.current_operation
        for error in event.result.errors:
            if should_ignore_error(schema_id, error, event):
                continue
            raise AssertionError(f"{event.current_operation}: {error.exception_with_traceback}")
    else:
        assert not event.result.errors, event.current_operation


def should_ignore_error(schema_id: str, error: SerializedError, event: events.AfterExecution) -> bool:
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
    if "Path parameter" in error.exception and error.exception.endswith("is not defined"):
        return True
    if "Malformed path template" in error.exception:
        return True
    if "Unresolvable JSON pointer in the schema" in error.exception:
        return True
    if RECURSIVE_REFERENCE_ERROR_MESSAGE in error.exception:
        return True
    if (schema_id, event.current_operation) in KNOWN_ISSUES:
        return True
    return False
