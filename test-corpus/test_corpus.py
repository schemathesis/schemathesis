import pathlib
import sys
from io import StringIO
from typing import NoReturn
from unittest.mock import patch

import pytest
import requests
from jsonschema.exceptions import SchemaError

import schemathesis
from schemathesis.checks import CHECKS
from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.run.handlers.cassettes import CassetteWriter
from schemathesis.cli.commands.run.handlers.junitxml import JunitXMLHandler
from schemathesis.config import HealthCheck
from schemathesis.config._report import ReportFormat
from schemathesis.core.compat import RefResolutionError
from schemathesis.core.errors import (
    IncorrectUsage,
    InvalidSchema,
    InvalidStateMachine,
    LoaderError,
    MalformedMediaType,
    OperationNotFound,
    format_exception,
)
from schemathesis.core.failures import Failure
from schemathesis.core.jsonschema import BundleError
from schemathesis.core.result import Ok
from schemathesis.core.transport import Response
from schemathesis.engine import Status, events, from_schema
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis.builder import _iter_coverage_cases
from schemathesis.specs.openapi.stateful import dependencies

CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
sys.path.append(str(CURRENT_DIR.parent))

from corpus.tools import json_loads, read_corpus_file  # noqa: E402

CORPUS_FILE_NAMES = (
    "swagger-2.0",
    "openapi-3.0",
    "openapi-3.1",
)
CORPUS_FILES = {name: read_corpus_file(name) for name in CORPUS_FILE_NAMES}

RESPONSE = Response(
    status_code=200,
    headers={"Content-Type": ["application/json"]},
    content=b"{}",
    request=requests.Request(method="GET", url="http://127.0.0.1/test").prepare(),
    elapsed=0.1,
    verify=False,
)
patch("schemathesis.Case.call", return_value=RESPONSE).start()


def pytest_generate_tests(metafunc):
    filenames = [(filename, member.name) for filename, corpus in CORPUS_FILES.items() for member in corpus.getmembers()]
    metafunc.parametrize("corpus, filename", filenames)


SLOW_DEFAULT = {
    "azure.com/devtestlabs-DTL/2016-05-15.json",
    "azure.com/network-applicationGateway/2015-06-15.json",
    "azure.com/network-applicationGateway/2016-09-01.json",
    "azure.com/network-applicationGateway/2016-12-01.json",
    "azure.com/network-applicationGateway/2017-03-01.json",
    "azure.com/network-applicationGateway/2017-06-01.json",
    "azure.com/network-applicationGateway/2017-08-01.json",
    "azure.com/network-applicationGateway/2017-09-01.json",
    "azure.com/network-applicationGateway/2017-10-01.json",
    "azure.com/network-applicationGateway/2017-11-01.json",
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
    "bungie.net/2.18.0.json",
    "kubernetes.io/unversioned.json",
    "kubernetes.io/v1.10.0.json",
    "microsoft.com/graph-beta/1.0.1.json",
    "microsoft.com/graph/1.0.1.json",
    "presalytics.io/ooxml/0.1.0.json",
    "stripe.com/2020-08-27.json",
    "stripe.com/2022-11-15.json",
}
SLOW_COVERAGE = {
    "adobe.com/aem/3.7.1-pre.0.json",
    "alertersystem.com/1.7.0.json",
    "amadeus.com/amadeus-hotel-booking/1.1.3.json",
    "amazonaws.com/acm-pca/2017-08-22.json",
    "amazonaws.com/acm/2015-12-08.json",
    "amazonaws.com/appflow/2020-08-23.json",
    "amazonaws.com/application-insights/2018-11-25.json",
    "amazonaws.com/appsync/2017-07-25.json",
    "amazonaws.com/autoscaling/2011-01-01.json",
    "amazonaws.com/ce/2017-10-25.json",
    "amazonaws.com/chime-sdk-meetings/2021-07-15.json",
    "amazonaws.com/chime-sdk-messaging/2021-05-15.json",
    "amazonaws.com/chime-sdk-voice/2022-08-03.json",
    "amazonaws.com/chime/2018-05-01.json",
    "amazonaws.com/cloudformation/2010-05-15.json",
    "amazonaws.com/cloudhsm/2014-05-30.json",
    "amazonaws.com/cloudtrail/2013-11-01.json",
    "amazonaws.com/codepipeline/2015-07-09.json",
    "amazonaws.com/codestar-connections/2019-12-01.json",
    "amazonaws.com/comprehend/2017-11-27.json",
    "amazonaws.com/config/2014-11-12.json",
    "amazonaws.com/connect/2017-08-08.json",
    "amazonaws.com/dataexchange/2017-07-25.json",
    "amazonaws.com/detective/2018-10-26.json",
    "amazonaws.com/discovery/2015-11-01.json",
    "amazonaws.com/drs/2020-02-26.json",
    "amazonaws.com/dynamodb/2012-08-10.json",
    "amazonaws.com/ec2/2016-11-15.json",
    "amazonaws.com/ecr-public/2020-10-30.json",
    "amazonaws.com/ecr/2015-09-21.json",
    "amazonaws.com/elasticache/2015-02-02.json",
    "amazonaws.com/elasticfilesystem/2015-02-01.json",
    "amazonaws.com/evidently/2021-02-01.json",
    "amazonaws.com/fsx/2018-03-01.json",
    "amazonaws.com/gamelift/2015-10-01.json",
    "amazonaws.com/glue/2017-03-31.json",
    "amazonaws.com/groundstation/2019-05-23.json",
    "amazonaws.com/health/2016-08-04.json",
    "amazonaws.com/iam/2010-05-08.json",
    "amazonaws.com/imagebuilder/2019-12-02.json",
    "amazonaws.com/inspector2/2020-06-08.json",
    "amazonaws.com/iot/2015-05-28.json",
    "amazonaws.com/iotfleetwise/2021-06-17.json",
    "amazonaws.com/iottwinmaker/2021-11-29.json",
    "amazonaws.com/kendra/2019-02-03.json",
    "amazonaws.com/kinesis-video-signaling/2019-12-04.json",
    "amazonaws.com/kinesisanalyticsv2/2018-05-23.json",
    "amazonaws.com/lambda/2015-03-31.json",
    "amazonaws.com/lex-models/2017-04-19.json",
    "amazonaws.com/logs/2014-03-28.json",
    "amazonaws.com/marketplace-catalog/2018-09-17.json",
    "amazonaws.com/mediaconvert/2017-08-29.json",
    "amazonaws.com/mediastore/2017-09-01.json",
    "amazonaws.com/mgn/2020-02-26.json",
    "amazonaws.com/models.lex.v2/2020-08-07.json",
    "amazonaws.com/monitoring/2010-08-01.json",
    "amazonaws.com/neptune/2014-10-31.json",
    "amazonaws.com/network-firewall/2020-11-12.json",
    "amazonaws.com/networkmanager/2019-07-05.json",
    "amazonaws.com/opensearchserverless/2021-11-01.json",
    "amazonaws.com/payment-cryptography-data/2022-02-03.json",
    "amazonaws.com/payment-cryptography/2021-09-14.json",
    "amazonaws.com/pi/2018-02-27.json",
    "amazonaws.com/pinpoint-sms-voice-v2/2022-03-31.json",
    "amazonaws.com/rds/2013-09-09.json",
    "amazonaws.com/rds/2014-09-01.json",
    "amazonaws.com/rds/2014-10-31.json",
    "amazonaws.com/redshift/2012-12-01.json",
    "amazonaws.com/rekognition/2016-06-27.json",
    "amazonaws.com/robomaker/2018-06-29.json",
    "amazonaws.com/rolesanywhere/2018-05-10.json",
    "amazonaws.com/s3/2006-03-01.json",
    "amazonaws.com/sagemaker/2017-07-24.json",
    "amazonaws.com/scheduler/2021-06-30.json",
    "amazonaws.com/securityhub/2018-10-26.json",
    "amazonaws.com/servicecatalog/2015-12-10.json",
    "amazonaws.com/sesv2/2019-09-27.json",
    "amazonaws.com/snowball/2016-06-30.json",
    "amazonaws.com/ssm-contacts/2021-05-03.json",
    "amazonaws.com/ssm/2014-11-06.json",
    "amazonaws.com/sso-admin/2020-07-20.json",
    "amazonaws.com/swf/2012-01-25.json",
    "amazonaws.com/transfer/2018-11-05.json",
    "amazonaws.com/verifiedpermissions/2021-12-01.json",
    "amazonaws.com/waf-regional/2016-11-28.json",
    "amazonaws.com/workdocs/2016-05-01.json",
    "amazonaws.com/workmail/2017-10-01.json",
    "api2cart.com/1.1.json",
    "apideck.com/accounting/10.0.0.json",
    "apideck.com/crm/10.0.0.json",
    "apideck.com/customer-support/9.5.0.json",
    "apideck.com/hris/10.0.0.json",
    "apigee.net/marketcheck-cars/2.01.json",
    "apisetu.gov.in/dittripura/3.0.0.json",
    "apisetu.gov.in/edistrictkerala/3.0.0.json",
    "apple.com/app-store-connect/1.4.1.json",
    "asana.com/1.0.json",
    "autotask.net/v1.json",
    "azure.com/cost-management-costmanagement/2018-05-31.json",
    "azure.com/cost-management-costmanagement/2018-08-01-preview.json",
    "azure.com/cost-management-costmanagement/2018-08-31.json",
    "azure.com/cost-management-costmanagement/2019-03-01-preview.json",
    "azure.com/cost-management-costmanagement/2019-04-01-preview.json",
    "azure.com/frontdoor/2018-08-01.json",
    "azure.com/frontdoor/2019-04-01.json",
    "azure.com/frontdoor/2019-05-01.json",
    "azure.com/frontdoor/2020-01-01.json",
    "azure.com/storage-DataLakeStorage/2018-06-17.json",
    "azure.com/storage-DataLakeStorage/2018-11-09.json",
    "azure.com/storage-DataLakeStorage/2019-10-31.json",
    "bbc.com/1.0.0.json",
    "beezup.com/2.0.json",
    "bigoven.com/partner.json",
    "bitbucket.org/2.0.json",
    "browshot.com/1.17.0.json",
    "cpy.re/peertube/5.1.0.json",
    "data2crm.com/1.json",
    "digitalnz.org/3.json",
    "docker.com/engine/1.33.json",
    "epa.gov/air/2019.10.15.json",
    "epa.gov/case/1.0.0.json",
    "epa.gov/cwa/2019.10.15.json",
    "epa.gov/echo/2019.10.15.json",
    "epa.gov/rcra/2019.10.15.json",
    "epa.gov/sdw/2019.10.15.json",
    "exhibitday.com/v1.json",
    "fec.gov/1.0.json",
    "flickr.com/1.0.0.json",
    "fraudlabspro.com/fraud-detection/1.1.json",
    "geodesystems.com/1.0.0.json",
    "gettyimages.com/3.json",
    "googleapis.com/admin/directory_v1.json",
    "googleapis.com/adsense/v2.json",
    "googleapis.com/aiplatform/v1.json",
    "googleapis.com/aiplatform/v1beta1.json",
    "googleapis.com/apigee/v1.json",
    "googleapis.com/blogger/v3.json",
    "googleapis.com/books/v1.json",
    "googleapis.com/businessprofileperformance/v1.json",
    "googleapis.com/calendar/v3.json",
    "googleapis.com/cloudasset/v1.json",
    "googleapis.com/cloudasset/v1p4beta1.json",
    "googleapis.com/clouderrorreporting/v1beta1.json",
    "googleapis.com/compute/alpha.json",
    "googleapis.com/compute/beta.json",
    "googleapis.com/compute/v1.json",
    "googleapis.com/container/v1.json",
    "googleapis.com/container/v1beta1.json",
    "googleapis.com/customsearch/v1.json",
    "googleapis.com/dfareporting/v3.3.json",
    "googleapis.com/dfareporting/v3.4.json",
    "googleapis.com/dfareporting/v4.json",
    "googleapis.com/dialogflow/v3.json",
    "googleapis.com/dialogflow/v3beta1.json",
    "googleapis.com/displayvideo/v1.json",
    "googleapis.com/displayvideo/v3.json",
    "googleapis.com/dlp/v2.json",
    "googleapis.com/documentai/v1beta3.json",
    "googleapis.com/drive/v2.json",
    "googleapis.com/drive/v3.json",
    "googleapis.com/drivelabels/v2beta.json",
    "googleapis.com/integrations/v1.json",
    "googleapis.com/integrations/v1alpha.json",
    "googleapis.com/migrationcenter/v1alpha1.json",
    "googleapis.com/monitoring/v3.json",
    "googleapis.com/people/v1.json",
    "googleapis.com/playdeveloperreporting/v1alpha1.json",
    "googleapis.com/playdeveloperreporting/v1beta1.json",
    "googleapis.com/run/v1.json",
    "googleapis.com/script/v1.json",
    "googleapis.com/sheets/v4.json",
    "googleapis.com/slides/v1.json",
    "googleapis.com/storage/v1.json",
    "googleapis.com/tagmanager/v1.json",
    "googleapis.com/tagmanager/v2.json",
    "googleapis.com/tasks/v1.json",
    "googleapis.com/vectortile/v1.json",
    "googleapis.com/vmmigration/v1alpha1.json",
    "googleapis.com/youtube/v3.json",
    "gov.bc.ca/geocoder/2.0.0.json",
    "graphhopper.com/1.0.0.json",
    "gsmtasks.com/2.4.13.json",
    "here.com/positioning/2.1.1.json",
    "hetras-certification.net/booking/v0.json",
    "hhs.gov/2.json",
    "hubapi.com/files/v3.json",
    "ideal-postcodes.co.uk/3.7.0.json",
    "image-charts.com/6.1.19.json",
    "influxdata.com/2.0.0.json",
    "intellifi.nl/2.23.4+0.gb463b49.dirty.json",
    "jellyfin.local/v1.json",
    "just-eat.co.uk/1.0.0.json",
    "keycloak.local/1.json",
    "keyserv.solutions/1.4.5.json",
    "linqr.app/2.0.json",
    "magento.com/2.2.10.json",
    "maif.local/otoroshi/1.5.0-dev.json",
    "microsoft.com/cognitiveservices-ImageSearch/1.0.json",
    "mist.com/0.37.7.json",
    "netbox.dev/3.4.json",
    "netboxdemo.com/2.4.json",
    "netboxdemo.com/2.8.json",
    "nordigen.com/2.0 (v2).json",
    "onsched.com/consumer/v1.json",
    "openaq.local/2.0.0.json",
    "openbankingproject.ch/1.3.8_2020-12-14 - Swiss edition 1.3.8.1-CH.json",
    "ote-godaddy.com/domains/1.0.0.json",
    "pandascore.co/2.23.1.json",
    "patientview.org/1.0.json",
    "redhat.local/patchman-engine/v1.15.3.json",
    "redirection.io/1.1.0.json",
    "reverb.com/3.0.json",
    "salesloft.com/v2.json",
    "schooldigger.com/v1.json",
    "schooldigger.com/v2.0.json",
    "shipengine.com/1.1.202304191404.json",
    "shutterstock.com/1.1.32.json",
    "simplyrets.com/1.0.0.json",
    "slideroom.com/v2.json",
    "snyk.io/1.0.0.json",
    "spoonacular.com/1.1.json",
    "spotify.com/1.0.0.json",
    "spotify.com/sonallux/2023.2.27.json",
    "squareup.com/2.0.json",
    "stream-io-api.com/v80.2.0.json",
    "svix.com/1.4.json",
    "telegram.org/5.0.0.json",
    "tfl.gov.uk/v1.json",
    "ticketmaster.com/discovery/v2.json",
    "tomtom.com/routing/1.0.0.json",
    "trello.com/1.0.json",
    "twilio.com/api/1.55.0.json",
    "twilio.com/twilio_insights_v1/1.55.0.json",
    "twinehealth.com/v7.78.1.json",
    "unicourt.com/1.0.0.json",
    "urlbox.io/v1.json",
    "velopayments.com/2.35.57.json",
    "visma.com/1.0.json",
    "visma.net/1.0.14.784.json",
    "visma.net/9.66.02.1023.json",
    "vocadb.net/1.0.json",
    "walletobjects.googleapis.com/pay-passes/v1.json",
    "warwick.ac.uk/enterobase/v2.0.json",
    "whapi.com/sportsdata/2.json",
    "zalando.com/v1.0.json",
    "zoom.us/2.0.0.json",
    "zuora.com/2021-08-20.json",
}
KNOWN_ISSUES = {
    # Regex that includes surrogates which is incompatible with the default alphabet for regex in Hypothesis (UTF-8)
    ("amazonaws.com/cleanrooms/2022-02-17.json", "POST /collaborations"),
    ("amazonaws.com/cleanrooms/2022-02-17.json", "POST /configuredTables"),
}


@schemathesis.check
def combined_check(ctx, response, case):
    case.as_curl_command()
    for check in CHECKS.get_all():
        if check is combined_check:
            continue
        try:
            check(ctx, response, case)
        except (Failure, SchemaError):
            pass


def test_default(corpus, filename):
    schema = _load_schema(corpus, filename)
    schema.config.update(suppress_health_check=list(HealthCheck))
    schema.config.phases.update(phases=["examples", "fuzzing"])
    schema.config.checks.update(included_check_names=[combined_check.__name__])

    handlers = [
        JunitXMLHandler(output=StringIO()),
        CassetteWriter(format=ReportFormat.VCR, output=StringIO(), config=schema.config),
        CassetteWriter(format=ReportFormat.HAR, output=StringIO(), config=schema.config),
    ]
    ctx = ExecutionContext(schema.config)

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


def test_coverage_phase(corpus, filename):
    if filename in SLOW_COVERAGE:
        pytest.skip("Data generation is extremely slow for this schema")
    schema = _load_schema(corpus, filename)
    modes = list(GenerationMode)
    for operation in schema.get_all_operations():
        if isinstance(operation, Ok):
            for _ in _iter_coverage_cases(
                operation=operation.ok(),
                generation_modes=modes,
                generate_duplicate_query_parameters=False,
                unexpected_methods=set(),
                generation_config=schema.config.generation,
            ):
                pass


def test_stateful(corpus, filename):
    schema = _load_schema(corpus, filename)

    # Test state machine creation and execution
    try:
        schema.as_state_machine()()
    except (
        RefResolutionError,
        IncorrectUsage,
        LoaderError,
        InvalidSchema,
        InvalidStateMachine,
        BundleError,
        MalformedMediaType,
        OperationNotFound,
    ):
        pass

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
    if "Cannot generate test data" in formatted:
        return True
    if "Failed to generate test cases from examples for this API operation" in formatted:
        return True
    if formatted.splitlines()[-1].startswith("Path parameters") and formatted.endswith("are not defined"):
        return True
    if "FailedHealthCheck" in formatted:
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
    if (schema_id, event.label) in KNOWN_ISSUES:
        return True
    return False
