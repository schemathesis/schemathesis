import os
import pathlib
import sys
import threading
from time import sleep
from typing import NoReturn

import hypothesis
import pytest
from aiohttp.test_utils import unused_port
from flask import Flask
from hypothesis import HealthCheck, Phase, Verbosity
from jsonschema import RefResolutionError

import schemathesis
from schemathesis._hypothesis._builder import _iter_coverage_cases
from schemathesis.checks import ALL_CHECKS
from schemathesis.constants import RECURSIVE_REFERENCE_ERROR_MESSAGE
from schemathesis.exceptions import CheckFailed, SchemaError, UsageError
from schemathesis.generation._methods import DataGenerationMethod
from schemathesis.internal.exceptions import format_exception
from schemathesis.internal.result import Err, Ok
from schemathesis.runner import events, from_schema
from schemathesis.runner.errors import EngineErrorInfo
from schemathesis.runner.models import Status
from schemathesis.service.client import ServiceClient
from schemathesis.service.constants import TOKEN_ENV_VAR, URL_ENV_VAR
from schemathesis.service.models import AnalysisError, SuccessState
from schemathesis.specs.openapi import loaders

CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
sys.path.append(str(CURRENT_DIR.parent))

from corpus.tools import json_loads, read_corpus_file  # noqa: E402

CORPUS_FILE_NAMES = (
    "swagger-2.0",
    "openapi-3.0",
    "openapi-3.1",
)
CORPUS_FILES = {name: read_corpus_file(name) for name in CORPUS_FILE_NAMES}
schemathesis.experimental.OPEN_API_3_1.enable()
VERIFY_SCHEMA_ANALYSIS = os.getenv("VERIFY_SCHEMA_ANALYSIS", "false").lower() in ("true", "1")
SCHEMATHESIS_IO_URL = os.getenv(URL_ENV_VAR)
SCHEMATHESIS_IO_TOKEN = os.getenv(TOKEN_ENV_VAR)


app = Flask("test_app")


def run_flask_app(app: Flask, port: int | None = None, timeout: float = 0.05) -> int:
    if port is None:
        port = unused_port()
    server_thread = threading.Thread(target=app.run, kwargs={"port": port})
    server_thread.daemon = True
    server_thread.start()
    sleep(timeout)
    return port


@app.route("/")
def default():
    return '{"success": true}'


def pytest_generate_tests(metafunc):
    filenames = [(filename, member.name) for filename, corpus in CORPUS_FILES.items() for member in corpus.getmembers()]
    metafunc.parametrize("corpus, filename", filenames)


SLOW = {
    "stripe.com/2020-08-27.json",
    "azure.com/network-applicationGateway/2018-08-01.json",
    "azure.com/network-applicationGateway/2019-06-01.json",
    "azure.com/network-applicationGateway/2017-11-01.json",
    "azure.com/network-applicationGateway/2019-02-01.json",
    "azure.com/network-applicationGateway/2017-10-01.json",
    "azure.com/network-applicationGateway/2019-07-01.json",
    "azure.com/network-applicationGateway/2018-12-01.json",
    "azure.com/network-applicationGateway/2018-02-01.json",
    "azure.com/network-applicationGateway/2019-08-01.json",
    "azure.com/network-applicationGateway/2018-06-01.json",
    "azure.com/network-applicationGateway/2018-07-01.json",
    "azure.com/network-applicationGateway/2015-06-15.json",
    "azure.com/network-applicationGateway/2018-04-01.json",
    "azure.com/network-applicationGateway/2017-09-01.json",
    "azure.com/network-applicationGateway/2018-10-01.json",
    "azure.com/network-applicationGateway/2018-11-01.json",
    "azure.com/network-applicationGateway/2016-12-01.json",
    "azure.com/network-applicationGateway/2018-01-01.json",
    "azure.com/network-applicationGateway/2017-08-01.json",
    "azure.com/network-applicationGateway/2017-03-01.json",
    "azure.com/network-applicationGateway/2019-04-01.json",
    "azure.com/network-applicationGateway/2016-09-01.json",
    "azure.com/network-applicationGateway/2017-06-01.json",
    "azure.com/web-WebApps/2018-02-01.json",
    "azure.com/web-WebApps/2019-08-01.json",
    "azure.com/web-WebApps/2018-11-01.json",
    "azure.com/web-WebApps/2016-08-01.json",
    "azure.com/devtestlabs-DTL/2016-05-15.json",
    "azure.com/devtestlabs-DTL/2018-09-15.json",
    "amazonaws.com/resource-groups/2017-11-27.json",
    "amazonaws.com/ivs/2020-07-14.json",
    "amazonaws.com/workspaces-web/2020-07-08.json",
    "presalytics.io/ooxml/0.1.0.json",
    "kubernetes.io/v1.10.0.json",
    "kubernetes.io/unversioned.json",
    "microsoft.com/graph/1.0.1.json",
    "microsoft.com/graph-beta/1.0.1.json",
    "wedpax.com/v1.json",
    "stripe.com/2022-11-15.json",
    "xero.com/xero-payroll-au/2.9.4.json",
    "xero.com/xero_accounting/2.9.4.json",
    "portfoliooptimizer.io/1.0.9.json",
    "amazonaws.com/proton/2020-07-20.json",
    "bungie.net/2.18.0.json",
    "amazonaws.com/sagemaker-geospatial/2020-05-27.json",
}
KNOWN_ISSUES = {
    # Regex that includes surrogates which is incompatible with the default alphabet for regex in Hypothesis (UTF-8)
    ("amazonaws.com/cleanrooms/2022-02-17.json", "POST /collaborations"),
    ("amazonaws.com/cleanrooms/2022-02-17.json", "POST /configuredTables"),
}


@pytest.fixture(scope="session")
def app_port():
    return run_flask_app(app)


def combined_check(ctx, response, case):
    case.as_curl_command()
    for check in ALL_CHECKS:
        try:
            check(ctx, response, case)
        except CheckFailed:
            pass


def test_default(corpus, filename, app_port):
    schema = _load_schema(corpus, filename, app_port)
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
        assert_event(filename, event)


def test_coverage_phase(corpus, filename):
    schema = _load_schema(corpus, filename)
    methods = DataGenerationMethod.all()
    for operation in schema.get_all_operations():
        if isinstance(operation, Ok):
            for _ in _iter_coverage_cases(operation.ok(), methods):
                pass


def _load_schema(corpus, filename, app_port=None):
    if filename in SLOW:
        pytest.skip("Data generation is extremely slow for this schema")
    raw_content = CORPUS_FILES[corpus].extractfile(filename)
    raw_schema = json_loads(raw_content.read())
    try:
        return loaders.from_dict(
            raw_schema,
            validate_schema=False,
            base_url=f"http://127.0.0.1:{app_port}/" if app_port is not None else None,
        )
    except SchemaError as exc:
        assert_invalid_schema(exc)


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
        assert not event.result.has_failures, event.verbose_name
        failures = [check for check in event.result.checks if check.value == Status.failure]
        assert not failures, event.verbose_name
        check_no_errors(schema_id, event)
        # Errors are checked above and unknown ones cause a test failure earlier
        assert event.status in (Status.success, Status.skip, Status.error)
    if isinstance(event, events.InternalError):
        raise AssertionError(f"Internal Error: {event.exception_with_traceback}")
    if VERIFY_SCHEMA_ANALYSIS and isinstance(event, events.AfterAnalysis):
        assert event.analysis is not None
        if isinstance(event.analysis, Err):
            traceback = format_exception(event.analysis.err(), with_traceback=True)
            raise AssertionError(f"Analysis failed: {traceback}")
        analysis = event.analysis.ok()
        assert not isinstance(analysis, AnalysisError)
        for extension in analysis.extensions:
            assert isinstance(extension.state, SuccessState), extension


def check_no_errors(schema_id: str, event: events.AfterExecution) -> None:
    if event.result.has_errors:
        assert event.result.errors, event.verbose_name
        for error in event.result.errors:
            if should_ignore_error(schema_id, error, event):
                continue
            raise AssertionError(f"{event.verbose_name}: {error.traceback}")
    else:
        assert not event.result.errors, event.verbose_name


def should_ignore_error(schema_id: str, error: EngineErrorInfo, event: events.AfterExecution) -> bool:
    if (
        schema_id == "launchdarkly.com/3.10.0.json" or schema_id == "launchdarkly.com/5.3.0.json"
    ) and "'<' not supported between instances" in str(error):
        return True
    if (
        "is not a 'regex'" in str(error)
        or "Invalid regular expression" in str(error)
        or "Invalid `pattern` value: expected a string" in str(error)
    ):
        return True
    if "Failed to generate test cases for this API operation" in str(error):
        return True
    if "Failed to generate test cases from examples for this API operation" in str(error):
        return True
    if "FailedHealthCheck" in str(error):
        return True
    if "Schemathesis can't serialize data" in str(error):
        return True
    if "Malformed media type" in str(error):
        return True
    if "Path parameter" in str(error) and str(error).endswith("is not defined"):
        return True
    if "Malformed path template" in str(error):
        return True
    if "Unresolvable JSON pointer in the schema" in str(error):
        return True
    if RECURSIVE_REFERENCE_ERROR_MESSAGE in str(error):
        return True
    if (schema_id, event.verbose_name) in KNOWN_ISSUES:
        return True
    return False
