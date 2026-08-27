import contextlib
import pathlib
import sys
from io import StringIO
from typing import NoReturn
from unittest.mock import patch

import pytest
import requests

import schemathesis
import schemathesis.graphql as _graphql
from schemathesis.checks import CHECKS
from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.run.handlers.har import HarHandler
from schemathesis.cli.commands.run.handlers.junitxml import JunitXMLHandler
from schemathesis.cli.commands.run.handlers.ndjson import NdjsonHandler
from schemathesis.cli.commands.run.handlers.vcr import VcrHandler
from schemathesis.config import HealthCheck
from schemathesis.core.errors import (
    IncorrectUsage,
    InvalidSchema,
    InvalidStateMachine,
    LoaderError,
    MalformedMediaType,
    OperationNotFound,
    RefResolutionError,
    format_exception,
)
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.core.jsonschema import BundleError
from schemathesis.core.transport import Response
from schemathesis.engine import Status, events, from_schema
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import TestPhase
from schemathesis.specs.openapi.stateful import dependencies

CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
sys.path.append(str(CURRENT_DIR.parent))

from tools.corpus.conformance import check_body_conformance  # noqa: E402
from tools.corpus.io import json_loads, read_corpus_file  # noqa: E402

CORPUS_FILE_NAMES = (
    "swagger-2.0",
    "openapi-3.0",
    "openapi-3.1",
)
CORPUS_FILES = {name: read_corpus_file(name) for name in CORPUS_FILE_NAMES}
GRAPHQL_CORPUS = read_corpus_file("graphql")

RESPONSE = Response(
    status_code=200,
    headers={"Content-Type": ["application/json"]},
    content=b"{}",
    request=requests.Request(method="GET", url="http://127.0.0.1/test").prepare(),
    elapsed=0.1,
    verify=False,
)


def _mock_case_call(case, *args, **kwargs):
    # Real `Case.call()` freezes metadata right before sending the request; mirror that here so
    # `case.meta` doesn't re-hash containers on every check access for large bodies.
    object.__setattr__(case, "_freeze_metadata", True)
    return RESPONSE


patch("schemathesis.Case.call", new=_mock_case_call).start()


def pytest_generate_tests(metafunc):
    if "corpus" not in metafunc.fixturenames:
        return
    filenames = [(filename, member.name) for filename, corpus in CORPUS_FILES.items() for member in corpus.getmembers()]
    metafunc.parametrize("corpus, filename", filenames)


SLOW_DEFAULT = {
    "microsoft.com/graph-beta/1.0.1.json",
    "microsoft.com/graph/1.0.1.json",
}
SLOW_COVERAGE = {
    "amazonaws.com/lex-models/2017-04-19.json",
    "amazonaws.com/lookoutequipment/2020-12-15.json",
    "amazonaws.com/worklink/2018-09-25.json",
    "azure.com/network-applicationGateway/2015-06-15.json",
    "azure.com/network-applicationGateway/2016-09-01.json",
    "azure.com/network-applicationGateway/2016-12-01.json",
    "azure.com/network-applicationGateway/2017-03-01.json",
    "azure.com/network-applicationGateway/2017-06-01.json",
    "azure.com/network-applicationGateway/2017-08-01.json",
    "azure.com/network-applicationGateway/2017-09-01.json",
    "azure.com/network-applicationGateway/2017-10-01.json",
    "azure.com/network-applicationGateway/2018-01-01.json",
    "azure.com/network-applicationGateway/2018-02-01.json",
    "azure.com/network-applicationGateway/2018-04-01.json",
    "azure.com/network-applicationGateway/2018-06-01.json",
    "azure.com/network-applicationGateway/2018-07-01.json",
    "azure.com/network-applicationGateway/2018-08-01.json",
    "azure.com/network-applicationGateway/2018-10-01.json",
    "azure.com/network-applicationGateway/2018-11-01.json",
    "azure.com/network-applicationGateway/2018-12-01.json",
    "azure.com/network-applicationGateway/2019-02-01.json",
    "azure.com/network-applicationGateway/2019-04-01.json",
    "azure.com/network-applicationGateway/2019-06-01.json",
    "azure.com/network-applicationGateway/2019-07-01.json",
    "azure.com/network-applicationGateway/2019-08-01.json",
    "kubernetes.io/unversioned.json",
    "kubernetes.io/v1.10.0.json",
}
KNOWN_ISSUES = {
    # Regex that includes surrogates which is incompatible with the default alphabet for regex in Hypothesis (UTF-8)
    ("amazonaws.com/cleanrooms/2022-02-17.json", "POST /collaborations"),
    ("amazonaws.com/cleanrooms/2022-02-17.json", "POST /configuredTables"),
}
# Coverage-phase JSON body conformance failures expected until each is fixed. Drive to zero.
# All entries here are Python-vs-Rust regex semantic differences — the Rust-backed generator
# emits strings the Python `re`-based validator rejects (Unicode whitespace/digits matched
# against ASCII char classes, nested-set `[[…]]` parsed differently, etc.) — or Rust regex
# engine limits (e.g. `^.{0,262144}$` panics in regex-automata as `HaystackTooLong`).
KNOWN_BODY_VIOLATIONS: set[tuple[str, str]] = {
    ("adyen.com/TerminalAPI-v1/1.json", "POST /display"),
    ("adyen.com/TerminalAPI-v1/1.json", "POST /enableservice"),
    ("adyen.com/TerminalAPI-v1/1.json", "POST /input"),
    ("adyen.com/TerminalAPI-v1/1.json", "POST /print"),
    ("amazonaws.com/account/2021-02-01.json", "POST /putAlternateContact"),
    ("amazonaws.com/appconfig/2019-10-09.json", "POST /extensions"),
    ("amazonaws.com/appconfig/2019-10-09.json", "PATCH /extensions/{ExtensionIdentifier}"),
    ("amazonaws.com/appstream/2016-12-01.json", "POST /#X-Amz-Target=PhotonAdminProxyService.CreateFleet"),
    ("amazonaws.com/appstream/2016-12-01.json", "POST /#X-Amz-Target=PhotonAdminProxyService.UpdateFleet"),
    ("amazonaws.com/auditmanager/2017-07-25.json", "POST /assessments"),
    ("amazonaws.com/auditmanager/2017-07-25.json", "PUT /assessments/{assessmentId}"),
    ("amazonaws.com/databrew/2017-07-25.json", "POST /datasets"),
    ("amazonaws.com/databrew/2017-07-25.json", "PUT /datasets/{name}"),
    # Pre-existing multi-slot pattern issue (unrelated to `update_quantifier`'s
    # balanced-distribution rewrite) — surfaced once that rewrite unblocked the earlier
    # ops on this schema.
    ("amazonaws.com/devops-guru/2020-12-01.json", "PUT /channels"),
    ("amazonaws.com/dlm/2018-01-12.json", "POST /policies"),
    ("amazonaws.com/dlm/2018-01-12.json", "PATCH /policies/{policyId}"),
    ("amazonaws.com/elastic-inference/2017-07-25.json", "POST /describe-accelerators"),
    (
        "amazonaws.com/emr-containers/2020-10-01.json",
        "POST /virtualclusters/{virtualClusterId}/endpoints/{endpointId}/credentials",
    ),
    ("amazonaws.com/forecast/2018-06-26.json", "POST /#X-Amz-Target=AmazonForecast.CreateAutoPredictor"),
    (
        "amazonaws.com/frauddetector/2019-11-15.json",
        "POST /#X-Amz-Target=AWSHawksNestServiceFacade.PutKMSEncryptionKey",
    ),
    ("amazonaws.com/gamesparks/2021-08-17.json", "POST /game/{GameName}/snapshot/{SnapshotId}/generated-sdk-code-job"),
    ("amazonaws.com/kms/2014-11-01.json", "POST /#X-Amz-Target=TrentService.CreateCustomKeyStore"),
    ("amazonaws.com/kms/2014-11-01.json", "POST /#X-Amz-Target=TrentService.UpdateCustomKeyStore"),
    ("amazonaws.com/lookoutmetrics/2017-07-25.json", "POST /DescribeAnomalyDetectionExecutions"),
    ("amazonaws.com/redshift-data/2019-12-20.json", "POST /#X-Amz-Target=RedshiftData.BatchExecuteStatement"),
    ("amazonaws.com/redshift-data/2019-12-20.json", "POST /#X-Amz-Target=RedshiftData.DescribeTable"),
    ("amazonaws.com/redshift-data/2019-12-20.json", "POST /#X-Amz-Target=RedshiftData.ExecuteStatement"),
    ("amazonaws.com/redshift-data/2019-12-20.json", "POST /#X-Amz-Target=RedshiftData.ListDatabases"),
    ("amazonaws.com/redshift-data/2019-12-20.json", "POST /#X-Amz-Target=RedshiftData.ListSchemas"),
    ("amazonaws.com/redshift-data/2019-12-20.json", "POST /#X-Amz-Target=RedshiftData.ListTables"),
    ("amazonaws.com/sagemaker-featurestore-runtime/2020-07-01.json", "POST /BatchGetRecord"),
    ("amazonaws.com/sagemaker-featurestore-runtime/2020-07-01.json", "PUT /FeatureGroup/{FeatureGroupName}"),
    (
        "amazonaws.com/translate/2017-07-01.json",
        "POST /#X-Amz-Target=AWSShineFrontendService_20170701.StartTextTranslationJob",
    ),
    # Pre-existing empty-object coverage case for a `oneOf` of a string enum and a required-property
    # object — surfaced once the discriminator pin stopped blocking the earlier ops on this schema.
    ("openai.com/2.3.0.json", "POST /threads/runs"),
    ("openai.com/2.3.0.json", "POST /threads/{thread_id}/runs"),
    ("restleague/market.json", "POST /register"),
    ("restleague/market.json", "PUT /customer/contacts"),
}


def _run_registered_checks(ctx, response, case):
    for check in CHECKS.get_all():
        if check in (combined_check, combined_check_coverage):
            continue
        with contextlib.suppress(Failure, FailureGroup):
            check(ctx, response, case)


def _check_body_conformance_violation(case):
    if case.meta is None or case.meta.phase.name != TestPhase.COVERAGE:
        return
    violation = check_body_conformance(case)
    if violation is None:
        return
    if violation.expected_valid:
        raise AssertionError(
            f"Positive coverage case produced an invalid body.\n"
            f"Media type: {violation.media_type}\n"
            f"Body: {violation.body!r}\n"
            f"Errors: {list(violation.errors)}"
        )
    raise AssertionError(
        f"Negative coverage case produced a valid body (mutation had no effect).\n"
        f"Media type: {violation.media_type}\n"
        f"Body: {violation.body!r}\n"
        f"Scenario: {case.meta.phase.data.scenario}"
    )


@schemathesis.check
def combined_check(ctx, response, case):
    case.as_curl_command()
    _run_registered_checks(ctx, response, case)
    _check_body_conformance_violation(case)


@schemathesis.check
def combined_check_coverage(ctx, response, case):
    # Coverage-phase case counts are orders of magnitude higher than fuzzing/examples, and
    # `as_curl_command` scans the whole body per case; skip it here to keep the test runnable.
    _run_registered_checks(ctx, response, case)
    _check_body_conformance_violation(case)


def test_default(corpus, filename):
    schema = _load_schema(corpus, filename)
    schema.config.update(suppress_health_check=list(HealthCheck))
    schema.config.phases.update(phases=["examples", "fuzzing"])
    schema.config.checks.update(included_check_names=[combined_check.__name__])

    handlers = [
        JunitXMLHandler(output=StringIO()),
        VcrHandler(output=StringIO(), config=schema.config.output),
        HarHandler(output=StringIO(), config=schema.config.output),
        NdjsonHandler(output=StringIO(), config=schema.config),
    ]
    ctx = ExecutionContext(schema.config)
    for handler in handlers:
        handler.start(ctx)

    try:
        for event in from_schema(schema).execute():
            if isinstance(event, events.Interrupted):
                pytest.exit("Keyboard Interrupt")
            assert_event(filename, event)
            for handler in handlers:
                handler.handle_event(ctx, event)
    finally:
        for handler in handlers:
            handler.shutdown(ctx)


@pytest.mark.parametrize("mode", [GenerationMode.POSITIVE, GenerationMode.NEGATIVE], ids=["positive", "negative"])
def test_coverage_phase(corpus, filename, mode):
    if filename in SLOW_COVERAGE:
        pytest.skip("Data generation is extremely slow for this schema")
    schema = _load_schema(corpus, filename)
    schema.config.update(suppress_health_check=list(HealthCheck))
    schema.config.phases.update(phases=["coverage"])
    schema.config.generation.update(modes=[mode])
    schema.config.checks.update(included_check_names=[combined_check_coverage.__name__])
    for event in from_schema(schema).execute():
        if isinstance(event, events.Interrupted):
            pytest.exit("Keyboard Interrupt")
        assert_event(filename, event)


def test_stateful(corpus, filename):
    schema = _load_schema(corpus, filename)

    # Test state machine creation and execution
    with contextlib.suppress(
        RefResolutionError,
        IncorrectUsage,
        LoaderError,
        InvalidSchema,
        InvalidStateMachine,
        BundleError,
        MalformedMediaType,
        OperationNotFound,
    ):
        schema.as_state_machine()()

    # Test dependency graph analysis and link iteration
    graph = dependencies.analyze(schema)
    for _ in graph.iter_links():
        pass


def _load_schema(corpus, filename):
    if filename in SLOW_DEFAULT:
        pytest.skip("Data generation is extremely slow for this schema")
    raw_content = CORPUS_FILES[corpus].extractfile(filename).read()
    raw_schema = json_loads(raw_content)
    try:
        schema = schemathesis.openapi.from_dict(raw_schema)
        schema.config.update(base_url="http://127.0.0.1:8080/")
        schema.config.generation.update(database="none", max_examples=1)
        schema.config.output.sanitization.update(enabled=False)
        return schema
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


_BODY_CONFORMANCE_FAILURE_PREFIXES = (
    "Positive coverage case produced an invalid body.",
    "Negative coverage case produced a valid body",
)
# Drained as entries fire; whatever remains at session end is now-passing rot.
_PENDING_BODY_VIOLATIONS: set[tuple[str, str]] = set(KNOWN_BODY_VIOLATIONS)


def _is_known_body_conformance_failure(schema_id: str, label: str, check) -> bool:
    if check.failure_info is None:
        return False
    failure_text = str(check.failure_info.failure)
    if not any(prefix in failure_text for prefix in _BODY_CONFORMANCE_FAILURE_PREFIXES):
        return False
    key = (schema_id, label)
    if key in KNOWN_BODY_VIOLATIONS:
        _PENDING_BODY_VIOLATIONS.discard(key)
        return True
    return False


def assert_event(schema_id: str, event: events.EngineEvent) -> None:
    if isinstance(event, events.NonFatalError) and not should_ignore_error(schema_id, event):
        raise AssertionError(f"{event.label}: {event.info.format()}")
    if isinstance(event, events.ScenarioFinished):
        all_failures = [
            check for checks in event.recorder.checks.values() for check in checks if check.status == Status.FAILURE
        ]
        failures = [
            check for check in all_failures if not _is_known_body_conformance_failure(schema_id, event.label, check)
        ]
        if failures:
            details = "\n\n".join(
                f"[{check.name}] {check.failure_info.failure}\n{check.failure_info.code_sample}"
                for check in failures
                if check.failure_info is not None
            )
            raise AssertionError(f"{event.label}: {len(failures)} check failure(s)\n\n{details}")
        # Suppressed body-conformance failures leave `event.status == FAILURE`; that's expected.
        if all_failures:
            return
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
    if "Cannot generate test data" in formatted:
        return True
    if "Failed to generate test cases from examples for this API operation" in formatted:
        return True
    if "Unknown GraphQL Scalar" in formatted:
        return True
    if formatted.splitlines()[-1].startswith("Path parameters") and formatted.endswith("are not defined"):
        return True
    if "Failed Health Check" in formatted:
        return True
    if "Serialization not possible" in formatted:
        return True
    if "Malformed media type" in formatted:
        return True
    if "Path parameter" in formatted and formatted.endswith("is not defined"):
        return True
    if "Malformed path template" in formatted:
        return True
    if "Unknown type:" in formatted:
        return True
    if "Unresolvable reference" in formatted:
        return True
    if "Unresolvable JSON pointer" in formatted:
        return True
    if "Ensure that the definition complies with the OpenAPI specification" in formatted:
        return True
    if "references non-existent operation" in formatted:
        return True
    if "is not defined in API operation" in formatted:
        return True
    if "is not in the specified alphabet" in formatted:
        return True
    if "Invalid Schema Object" in formatted:
        return True
    if "contain invalid link definitions" in formatted:
        return True
    if "Cannot bundle" in formatted:
        return True
    if "required references forming a cycle" in formatted or "required reference to itself" in formatted:
        return True
    if "cannot be resolved" in formatted:
        return True
    return (schema_id, event.label) in KNOWN_ISSUES


GRAPHQL_FILENAMES = [member.name for member in GRAPHQL_CORPUS.getmembers()]


@pytest.mark.parametrize("filename", GRAPHQL_FILENAMES)
def test_graphql(filename):
    raw_content = GRAPHQL_CORPUS.extractfile(filename).read()
    raw_schema = json_loads(raw_content)
    schema = _graphql.from_dict(raw_schema)
    schema.config.update(base_url="http://127.0.0.1:8080/graphql")
    schema.config.generation.update(database=None, max_examples=1)
    schema.config.output.sanitization.update(enabled=False)
    schema.config.update(suppress_health_check=list(HealthCheck))
    schema.config.phases.update(phases=["fuzzing"])
    schema.config.checks.update(included_check_names=[combined_check.__name__])

    handlers = [
        JunitXMLHandler(output=StringIO()),
        VcrHandler(output=StringIO(), config=schema.config.output),
        HarHandler(output=StringIO(), config=schema.config.output),
        NdjsonHandler(output=StringIO(), config=schema.config),
    ]
    ctx = ExecutionContext(schema.config)
    for handler in handlers:
        handler.start(ctx)

    try:
        for event in from_schema(schema).execute():
            if isinstance(event, events.Interrupted):
                pytest.exit("Keyboard Interrupt")
            assert_event(filename, event)
            for handler in handlers:
                handler.handle_event(ctx, event)
    finally:
        for handler in handlers:
            handler.shutdown(ctx)
