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

import schemathesis
from schemathesis.checks import CHECKS
from schemathesis.core.compat import RefResolutionError
from schemathesis.core.errors import (
    RECURSIVE_REFERENCE_ERROR_MESSAGE,
    IncorrectUsage,
    InvalidSchema,
    InvalidStateMachine,
    LoaderError,
    format_exception,
)
from schemathesis.core.failures import Failure
from schemathesis.core.result import Ok
from schemathesis.engine import Status, events, from_schema
from schemathesis.engine.config import EngineConfig, ExecutionConfig
from schemathesis.engine.phases import PhaseName
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis.builder import _iter_coverage_cases

CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
sys.path.append(str(CURRENT_DIR.parent))

from corpus.tools import json_loads, read_corpus_file  # noqa: E402

CORPUS_FILE_NAMES = (
    "swagger-2.0",
    "openapi-3.0",
    "openapi-3.1",
)
CORPUS_FILES = {name: read_corpus_file(name) for name in CORPUS_FILE_NAMES}


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
    for check in CHECKS.get_all():
        try:
            check(ctx, response, case)
        except Failure:
            pass


def test_default(corpus, filename, app_port):
    schema = _load_schema(corpus, filename, app_port)
    try:
        schema.as_state_machine()()
    except (RefResolutionError, IncorrectUsage, LoaderError, InvalidSchema, InvalidStateMachine):
        pass

    engine = from_schema(
        schema,
        config=EngineConfig(
            execution=ExecutionConfig(
                phases=[PhaseName.EXAMPLES, PhaseName.FUZZING],
                checks=[combined_check],
                hypothesis_settings=hypothesis.settings(
                    deadline=None,
                    database=None,
                    max_examples=1,
                    suppress_health_check=list(HealthCheck),
                    phases=[Phase.explicit, Phase.generate],
                    verbosity=Verbosity.quiet,
                ),
            )
        ),
    )
    for event in engine.execute():
        if isinstance(event, events.Interrupted):
            pytest.exit("Keyboard Interrupt")
        assert_event(filename, event)


def test_coverage_phase(corpus, filename):
    schema = _load_schema(corpus, filename)
    modes = GenerationMode.all()
    for operation in schema.get_all_operations():
        if isinstance(operation, Ok):
            for _ in _iter_coverage_cases(operation.ok(), modes):
                pass


def _load_schema(corpus, filename, app_port=None):
    if filename in SLOW:
        pytest.skip("Data generation is extremely slow for this schema")
    raw_content = CORPUS_FILES[corpus].extractfile(filename)
    raw_schema = json_loads(raw_content.read())
    try:
        return schemathesis.openapi.from_dict(raw_schema).configure(
            base_url=f"http://127.0.0.1:{app_port}/" if app_port is not None else None,
        )
    except LoaderError as exc:
        assert_invalid_schema(exc)


def assert_invalid_schema(exc: LoaderError) -> NoReturn:
    error = str(exc.__cause__)
    if (
        "while scanning a block scalar" in error
        or "while parsing a block mapping" in error
        or "could not determine a constructor for the tag" in error
        or "unacceptable character" in error
    ):
        pytest.skip("Invalid schema")
    raise exc


def assert_event(schema_id: str, event: events.EngineEvent) -> None:
    if isinstance(event, events.NonFatalError):
        if not should_ignore_error(schema_id, event):
            raise AssertionError(f"{event.label}: {event.info.format()}")
    if isinstance(event, events.ScenarioFinished):
        failures = [
            check for checks in event.recorder.checks.values() for check in checks if check.status == Status.FAILURE
        ]
        assert not failures
        # Errors are checked above and unknown ones cause a test failure earlier
        assert event.status in (Status.SUCCESS, Status.SKIP, Status.ERROR)
    if isinstance(event, events.FatalError):
        raise AssertionError(f"Internal Error: {format_exception(event.exception, with_traceback=True)}")


def should_ignore_error(schema_id: str, event: events.NonFatalError) -> bool:
    formatted = event.info.format()
    if (
        schema_id == "launchdarkly.com/3.10.0.json" or schema_id == "launchdarkly.com/5.3.0.json"
    ) and "'<' not supported between instances" in formatted:
        return True
    if (
        "is not a 'regex'" in formatted
        or "Invalid regular expression" in formatted
        or "Invalid `pattern` value: expected a string" in formatted
    ):
        return True
    if "Failed to generate test cases for this API operation" in formatted:
        return True
    if "Failed to generate test cases from examples for this API operation" in formatted:
        return True
    if "FailedHealthCheck" in formatted:
        return True
    if "Schemathesis can't serialize data" in formatted:
        return True
    if "Malformed media type" in formatted:
        return True
    if "Path parameter" in formatted and formatted.endswith("is not defined"):
        return True
    if "Malformed path template" in formatted:
        return True
    if "Unresolvable JSON pointer" in formatted:
        return True
    if "Ensure that the definition complies with the OpenAPI specification" in formatted:
        return True
    if "references non-existent operation" in formatted:
        return True
    if "is not defined in API operation" in formatted:
        return True
    if "contain invalid link definitions" in formatted:
        return True
    if RECURSIVE_REFERENCE_ERROR_MESSAGE in formatted:
        return True
    if (schema_id, event.label) in KNOWN_ISSUES:
        return True
    return False
