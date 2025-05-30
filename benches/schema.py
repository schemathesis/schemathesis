import pathlib
import sys
from unittest.mock import patch

import pytest
import requests

import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.core.transforms import deepclone
from schemathesis.core.transport import Response
from schemathesis.engine import from_schema

CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
sys.path.append(str(CURRENT_DIR.parent))
CATALOG_DIR = CURRENT_DIR / "data"

from corpus.tools import load_from_corpus, read_corpus_file  # noqa: E402

CONFIG = SchemathesisConfig()

CORPUS_OPENAPI_30 = read_corpus_file("openapi-3.0")
CORPUS_SWAGGER_20 = read_corpus_file("swagger-2.0")
# Small size (~2k lines in YAML)
BBCI = load_from_corpus("bbci.co.uk/1.0.json", CORPUS_OPENAPI_30)
BBCI_SCHEMA = schemathesis.openapi.from_dict(BBCI)
BBCI_OPERATIONS = list(BBCI_SCHEMA.get_all_operations())
# Medium size (~8k lines in YAML)
VMWARE = load_from_corpus("vmware.local/vrni/1.0.0.json", CORPUS_OPENAPI_30)
VMWARE_SCHEMA = schemathesis.openapi.from_dict(VMWARE)
VMWARE_OPERATIONS = list(VMWARE_SCHEMA.get_all_operations())
# Large size (~92k lines in YAML)
STRIPE = load_from_corpus("stripe.com/2022-11-15.json", CORPUS_OPENAPI_30)
STRIPE_SCHEMA = schemathesis.openapi.from_dict(STRIPE)
# Medium GraphQL schema (~6k lines)
UNIVERSE = load_from_corpus("universe.json", "graphql")
UNIVERSE_SCHEMA = schemathesis.graphql.from_dict(UNIVERSE)

APPVEYOR = load_from_corpus("appveyor.com/1.0.0.json", CORPUS_SWAGGER_20)
EVETECH = load_from_corpus("evetech.net/0.8.6.json", CORPUS_SWAGGER_20)
OSISOFT = load_from_corpus("osisoft.com/1.11.1.5383.json", CORPUS_SWAGGER_20)
ML_WEBSERVICES = load_from_corpus("azure.com/machinelearning-webservices/2017-01-01.json", CORPUS_SWAGGER_20)
AZURE_NETWORK = load_from_corpus("azure.com/network/2016-03-30.json", CORPUS_SWAGGER_20)

RESPONSE = Response(
    status_code=200,
    headers={},
    content=b"",
    request=requests.Request(method="GET", url="http://127.0.0.1/test").prepare(),
    elapsed=0.1,
    verify=False,
)
patch("schemathesis.Case.call", return_value=RESPONSE).start()


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "raw_schema, loader",
    [
        (BBCI, schemathesis.openapi.from_dict),
        (VMWARE, schemathesis.openapi.from_dict),
        (UNIVERSE, schemathesis.graphql.from_dict),
        (APPVEYOR, schemathesis.openapi.from_dict),
        (EVETECH, schemathesis.openapi.from_dict),
        (OSISOFT, schemathesis.openapi.from_dict),
        (ML_WEBSERVICES, schemathesis.openapi.from_dict),
        (AZURE_NETWORK, schemathesis.openapi.from_dict),
    ],
    ids=("bbci", "vmware", "universe", "appveyor", "evetech", "osisoft", "ml_webservices", "azure_network"),
)
def test_iter_operations(raw_schema, loader):
    schema = loader(raw_schema, config=CONFIG)

    for _ in schema.get_all_operations():
        pass


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "raw_schema, loader",
    [
        (BBCI, schemathesis.openapi.from_dict),
        (VMWARE, schemathesis.openapi.from_dict),
        (STRIPE, schemathesis.openapi.from_dict),
        (UNIVERSE, schemathesis.graphql.from_dict),
        (APPVEYOR, schemathesis.openapi.from_dict),
        (EVETECH, schemathesis.openapi.from_dict),
        (OSISOFT, schemathesis.openapi.from_dict),
        (ML_WEBSERVICES, schemathesis.openapi.from_dict),
        (AZURE_NETWORK, schemathesis.openapi.from_dict),
    ],
    ids=("bbci", "vmware", "stripe", "universe", "appveyor", "evetech", "osisoft", "ml_webservices", "azure_network"),
)
def test_length(raw_schema, loader):
    schema = loader(raw_schema, config=CONFIG)
    _ = len(schema)


# Schemas with pre-populated cache
BBCI_OPERATION_ID = "Get_Categories_"
BBCI_OPERATION_KEY = ("/categories", "get")
BBCI_SCHEMA_WITH_OPERATIONS_CACHE = schemathesis.openapi.from_dict(BBCI)
BBCI_SCHEMA_WITH_OPERATIONS_CACHE.get_operation_by_id(BBCI_OPERATION_ID)
VMWARE_OPERATION_ID = "listProblemEvents"
VMWARE_OPERATION_KEY = ("/entities/problems", "get")
VMWARE_SCHEMA_WITH_OPERATIONS_CACHE = schemathesis.openapi.from_dict(VMWARE)
VMWARE_SCHEMA_WITH_OPERATIONS_CACHE.get_operation_by_id(VMWARE_OPERATION_ID)
UNIVERSE_OPERATION_KEY = ("Query", "manageTickets")
UNIVERSE_SCHEMA_WITH_OPERATIONS_CACHE = schemathesis.graphql.from_dict(UNIVERSE)
UNIVERSE_SCHEMA_WITH_OPERATIONS_CACHE[UNIVERSE_OPERATION_KEY[0]][UNIVERSE_OPERATION_KEY[1]]


BBCI_SCHEMA.config.generation.update(max_examples=1)
BBCI_SCHEMA.config.phases.update(phases=["examples", "fuzzing"])


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "raw_schema, key, loader",
    [
        (BBCI, BBCI_OPERATION_KEY, schemathesis.openapi.from_dict),
        (VMWARE, VMWARE_OPERATION_KEY, schemathesis.openapi.from_dict),
        (UNIVERSE, UNIVERSE_OPERATION_KEY, schemathesis.graphql.from_dict),
    ],
    ids=("bbci", "vmware", "universe"),
)
def test_get_operation_single(raw_schema, key, loader):
    schema = loader(raw_schema, config=CONFIG)
    current = schema
    for segment in key:
        current = current[segment]


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "schema, key",
    [
        (BBCI_SCHEMA_WITH_OPERATIONS_CACHE, BBCI_OPERATION_KEY),
        (VMWARE_SCHEMA_WITH_OPERATIONS_CACHE, VMWARE_OPERATION_KEY),
        (UNIVERSE_SCHEMA_WITH_OPERATIONS_CACHE, UNIVERSE_OPERATION_KEY),
    ],
    ids=("bbci", "vmware", "universe"),
)
def test_get_operation_repeatedly(schema, key):
    current = schema
    for segment in key:
        current = current[segment]


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "raw_schema, key",
    [(BBCI, BBCI_OPERATION_ID), (VMWARE, VMWARE_OPERATION_ID)],
    ids=("bbci", "vmware"),
)
def test_get_operation_by_id_single(raw_schema, key):
    schema = schemathesis.openapi.from_dict(raw_schema, config=CONFIG)
    _ = schema.get_operation_by_id(key)


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "schema, key",
    [
        (BBCI_SCHEMA_WITH_OPERATIONS_CACHE, BBCI_OPERATION_ID),
        (VMWARE_SCHEMA_WITH_OPERATIONS_CACHE, VMWARE_OPERATION_ID),
    ],
    ids=("bbci", "vmware"),
)
def test_get_operation_by_id_repeatedly(schema, key):
    _ = schema.get_operation_by_id(key)


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "raw_schema, key",
    [
        (BBCI, "#/paths/~1categories/get"),
        (VMWARE, "#/paths/~1entities~1problems/get"),
    ],
    ids=("bbci", "vmware"),
)
def test_get_operation_by_reference_single(raw_schema, key):
    schema = schemathesis.openapi.from_dict(raw_schema, config=CONFIG)
    _ = schema.get_operation_by_reference(key)


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "schema, key",
    [
        (BBCI_SCHEMA_WITH_OPERATIONS_CACHE, "#/paths/~1categories/get"),
        (VMWARE_SCHEMA_WITH_OPERATIONS_CACHE, "#/paths/~1entities~1problems/get"),
    ],
    ids=("bbci", "vmware"),
)
def test_get_operation_by_reference_repeatedly(schema, key):
    _ = schema.get_operation_by_reference(key)


@pytest.mark.benchmark
@pytest.mark.parametrize("operations", [BBCI_OPERATIONS, VMWARE_OPERATIONS], ids=("bbci", "vmware"))
def test_as_json_schema(operations):
    for operation in operations:
        for parameter in operation.ok().iter_parameters():
            _ = parameter.as_json_schema(operation)


@pytest.mark.benchmark
def test_events():
    engine = from_schema(BBCI_SCHEMA)
    for _ in engine.execute():
        pass


@pytest.mark.benchmark
@pytest.mark.parametrize("raw_schema", [BBCI, VMWARE, STRIPE], ids=("bbci", "vmware", "stripe"))
def test_rewritten_components(raw_schema):
    schema = schemathesis.openapi.from_dict(raw_schema, config=CONFIG)

    _ = schema.rewritten_components


@pytest.mark.benchmark
@pytest.mark.parametrize("raw_schema", [BBCI, VMWARE, STRIPE], ids=("bbci", "vmware", "stripe"))
def test_links_count(raw_schema):
    schema = schemathesis.openapi.from_dict(raw_schema, config=CONFIG)

    _ = schema.statistic.links.total


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "schema",
    [
        BBCI,
        VMWARE,
        STRIPE,
        UNIVERSE,
        APPVEYOR,
        EVETECH,
        OSISOFT,
        ML_WEBSERVICES,
        AZURE_NETWORK,
    ],
    ids=("bbci", "vmware", "stripe", "universe", "appveyor", "evetech", "osisoft", "ml_webservices", "azure_network"),
)
def test_deepclone(schema):
    deepclone(schema)
