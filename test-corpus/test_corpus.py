import json
import pathlib
from typing import Any, Dict

import hypothesis
import pytest
from hypothesis import HealthCheck, Phase

from schemathesis.constants import RECURSIVE_REFERENCE_ERROR_MESSAGE
from schemathesis.runner import events, from_schema
from schemathesis.specs.openapi import loaders

CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
CATALOG_DIR = CURRENT_DIR / "openapi-directory/APIs/"


def read_file(filename: str) -> Dict[str, Any]:
    with (CURRENT_DIR / filename).open() as fd:
        return json.load(fd)


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


AZURE_FAILING_SCHEMAS = (
    f"azure.com/network-networkProfile/{date}/swagger.yaml"
    for date in (
        "2018-10-01",
        "2018-11-01",
        "2018-12-01",
        "2019-02-01",
        "2019-04-01",
        "2019-06-01",
        "2019-07-01",
        "2019-08-01",
    )
)
XFAILING = {
    # https://github.com/schemathesis/schemathesis/issues/986
    schema: {
        "PUT /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Network/"
        "networkProfiles/{networkProfileName}": "hypothesis-jsonschema does not fetch remote references",
    }
    for schema in AZURE_FAILING_SCHEMAS
}

# These are too big, but can pass
FLAKY_SCHEMAS = read_file("flaky.json")
# These schemas reference local files that are not present.
# Schemas are versioned by date, the final path looks like this:
# `azure.com/{service}/{date}/swagger.yaml`
INCOMPLETE_AZURE_SCHEMAS = read_file("incomplete_azure.json")
REFERENCE_ERROR_MISSING_FILE = (
    "jsonschema.exceptions.RefResolutionError: <urlopen error [Errno 2] No such file or directory"
)
NOT_PARSABLE_SCHEMAS = {
    "zenoti.com/1.0.0/swagger.yaml": "yaml.scanner.ScannerError: while scanning a block scalar",
    "epa.gov/eff/2019.10.15/swagger.yaml": "ConstructorError: could not determine a constructor for the tag",
    # `18_24` is parsed as 1824, but it should be a string
    "statsocial.com/1.0.0/swagger.yaml": "jsonschema.exceptions.RefResolutionError: Unresolvable JSON pointer",
    "azure.com/cognitiveservices-LUIS-Authoring/2.0/swagger.yaml": "yaml.constructor.ConstructorError: could not determine a constructor for the tag",
    "azure.com/cognitiveservices-LUIS-Authoring/3.0-preview/swagger.yaml": "yaml.constructor.ConstructorError: could not determine a constructor for the tag",
    "azure.com/cognitiveservices-LUIS-Programmatic/v2.0/swagger.yaml": "yaml.constructor.ConstructorError: could not determine a constructor for the tag",
    "atlassian.com/jira/1001.0.0-SNAPSHOT/openapi.yaml": "yaml.constructor.ConstructorError: could not determine a constructor for the tag",
    "akeneo.com/1.0.0/swagger.yaml": "yaml.constructor.ConstructorError: could not determine a constructor for the tag",
    "adyen.com/PaymentService/30/openapi.yaml": "found a tab character where an indentation space is expected",
    "adyen.com/PaymentService/40/openapi.yaml": "found a tab character where an indentation space is expected",
    "adyen.com/CheckoutService/40/openapi.yaml": "found a tab character where an indentation space is expected",
    "bunq.com/1.0/openapi.yaml": "yaml.parser.ParserError: while parsing a block mapping",
    "adyen.com/CheckoutService/46/openapi.yaml": "yaml.scanner.ScannerError: while scanning a block scalar",
    "adyen.com/CheckoutService/41/openapi.yaml": "yaml.scanner.ScannerError: while scanning a block scalar",
    "adyen.com/CheckoutService/37/openapi.yaml": "yaml.scanner.ScannerError: while scanning a block scalar",
    "adyen.com/PaymentService/46/openapi.yaml": "yaml.scanner.ScannerError: while scanning a block scalar",
    "adyen.com/PaymentService/25/openapi.yaml": "yaml.scanner.ScannerError: while scanning a block scalar",
    "docusign.net/v2/swagger.yaml": "yaml.reader.ReaderError: unacceptable character #x0080: control characters are not allowed",
    **{
        f"azure.com/{service}/{date}/swagger.yaml": REFERENCE_ERROR_MISSING_FILE
        for service, dates in INCOMPLETE_AZURE_SCHEMAS.items()
        for date in dates
    },
}
INVALID_SCHEMAS = (
    # `on` is parsed as `True` and makes the canonicalisation encoder to fail
    "victorops.com/0.0.3/swagger.yaml",
    "launchdarkly.com/3.10.0/swagger.yaml",
)
RECURSIVE_REFERENCES = read_file("recursive_references.json")

UNSATISFIABLE_SCHEMAS = {
    "amazonaws.com/s3control/2018-08-20/openapi.yaml": (
        # Too broad regex that lead to excessive filtering
        "DELETE /v20180820/jobs/{id}/tagging#x-amz-account-id",
        "GET /v20180820/jobs/{id}/tagging#x-amz-account-id",
        "PUT /v20180820/jobs/{id}/tagging#x-amz-account-id",
        "GET /v20180820/jobs/{id}#x-amz-account-id",
        "POST /v20180820/jobs/{id}/priority#x-amz-account-id&priority",
        "POST /v20180820/jobs/{id}/status#x-amz-account-id&requestedJobStatus",
    ),
    "amazonaws.com/application-insights/2018-11-25/openapi.yaml": (
        "POST /#X-Amz-Target=EC2WindowsBarleyService.UntagResource",
    ),
    "amazonaws.com/elasticfilesystem/2015-02-01/openapi.yaml": (
        "POST /2015-02-01/delete-tags/{FileSystemId}",
        "DELETE /2015-02-01/resource-tags/{ResourceId}#tagKeys",
    ),
    "amazonaws.com/codestar/2017-04-19/openapi.yaml": (
        # Regex contains a value that is not properly escaped
        "POST /#X-Amz-Target=CodeStar_20170419.CreateUserProfile",
    ),
    "amazonaws.com/cloudhsm/2014-05-30/openapi.yaml": (
        # Regex + min/max length that leads to excessive filtering
        "POST /#X-Amz-Target=CloudHsmFrontendService.CreateLunaClient",
        "POST /#X-Amz-Target=CloudHsmFrontendService.ModifyLunaClient",
    ),
    "shipengine.com/1.1.202006302006/openapi.yaml": (
        # Inaccurate usage of `additionalProperties: False` + `allOf` that lead to a schema that is impossible to
        # satisfy
        "PUT /v1/carriers/{carrier_id}/add_funds",
        "PATCH /v1/insurance/shipsurance/add_funds",
        "POST /v1/labels",
        "POST /v1/packages",
        "PUT /v1/packages/{package_id}",
        "POST /v1/rates",
        "POST /v1/rates/bulk",
        "POST /v1/rates/estimate",
        "POST /v1/shipments",
        "PUT /v1/shipments",
        "PUT /v1/shipments/{shipment_id}",
        "POST /v1/warehouses",
        "PUT /v1/warehouses/{warehouse_id}",
    ),
    "windows.net/graphrbac/1.6/swagger.yaml": (
        # `additionalProperties` conflicts with some properties
        "POST /{tenantID}/users",
    ),
}


def add_schemas(filename):
    for schema_id, operations in read_file(filename).items():
        if schema_id in UNSATISFIABLE_SCHEMAS:
            UNSATISFIABLE_SCHEMAS[schema_id] += operations
        else:
            UNSATISFIABLE_SCHEMAS[schema_id] = operations


for name in ("invalid_path_parameters.json", "incompatible_regex.json", "incompatible_enums.json"):
    add_schemas(name)


def test_runner(schema_path):
    schema = loaders.from_path(schema_path, validate_schema=False)
    runner = from_schema(
        schema,
        dry_run=True,
        count_operations=False,
        hypothesis_settings=hypothesis.settings(
            max_examples=1, suppress_health_check=HealthCheck.all(), phases=[Phase.explicit, Phase.generate]
        ),
    )

    schema_id = get_id(schema_path)

    def check_xfailed(ev) -> bool:
        if schema_id in XFAILING and ev.current_operation in XFAILING[schema_id]:
            if ev.result.errors:
                message = XFAILING[schema_id][ev.current_operation]
                # If failed for some other reason, then an assertion will be risen
                return any(message in err.exception_with_traceback for err in ev.result.errors)
            pytest.fail("Expected a failure")
        return False

    def is_unsatisfiable(text):
        return "Unable to satisfy schema parameters for this API operation" in text

    def check_flaky(ev) -> bool:
        if schema_id in FLAKY_SCHEMAS and ev.current_operation in FLAKY_SCHEMAS[schema_id]:
            if ev.result.errors:
                # NOTE. There could be other errors if the "Unsatisfiable" case wasn't triggered.
                # Could be added to expected errors later
                return any(is_unsatisfiable(err.exception_with_traceback) for err in ev.result.errors)
        return False

    def check_unsatisfiable(ev):
        # In some cases Schemathesis can't generate data - either due to a contradiction within the schema
        if schema_id in UNSATISFIABLE_SCHEMAS and ev.current_operation in UNSATISFIABLE_SCHEMAS[schema_id]:
            exception = ev.result.errors[0].exception
            if (
                "Unable to satisfy schema parameters for this API operation" not in exception
                and "Cannot create non-empty lists with elements" not in exception
                and "Cannot create a collection of " not in exception
            ):
                pytest.fail(f"Expected unsatisfiable, but there is a different error: {exception}")
            return True
        return False

    def check_recursive_references(ev):
        if schema_id in RECURSIVE_REFERENCES and ev.current_operation in RECURSIVE_REFERENCES[schema_id]:
            for err in ev.result.errors:
                if RECURSIVE_REFERENCE_ERROR_MESSAGE in err.exception_with_traceback:
                    # It is OK
                    return True
            # These errors may be triggered not every time
        return False

    def check_not_parsable(ev):
        return schema_id in NOT_PARSABLE_SCHEMAS and NOT_PARSABLE_SCHEMAS[schema_id] in ev.exception_with_traceback

    def check_invalid(ev):
        if schema_id in INVALID_SCHEMAS:
            if ev.result.errors:
                return any(
                    "The API schema contains non-string keys" in err.exception_with_traceback
                    for err in ev.result.errors
                )
            return pytest.fail("Expected YAML parsing error")
        return False

    for event in runner.execute():
        if isinstance(event, events.AfterExecution):
            if check_xfailed(event):
                continue
            if check_flaky(event):
                continue
            if check_recursive_references(event):
                continue
            if check_unsatisfiable(event):
                continue
            if check_invalid(event):
                continue
            assert not event.result.has_errors, event.current_operation
            assert not event.result.has_failures, event.current_operation
        if isinstance(event, events.InternalError):
            if check_not_parsable(event):
                continue
        assert not isinstance(event, events.InternalError), event.exception_with_traceback
