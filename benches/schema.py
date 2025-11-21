import json
import pathlib
import sys
from io import StringIO
from queue import Queue
from unittest.mock import patch

import pytest
import requests

import schemathesis
from schemathesis.cli.commands.run.handlers.cassettes import Finalize, Initialize, Process, har_writer, vcr_writer
from schemathesis.config import SchemathesisConfig
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import deepclone
from schemathesis.core.transport import Response
from schemathesis.engine import events, from_schema
from schemathesis.generation.hypothesis import setup
from schemathesis.generation.modes import GenerationMode
from schemathesis.specs.openapi._hypothesis import get_parameters_strategy
from schemathesis.specs.openapi.stateful import dependencies
from schemathesis.specs.openapi.stateful.dependencies.layers import compute_dependency_layers

CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
sys.path.append(str(CURRENT_DIR.parent))
CATALOG_DIR = CURRENT_DIR / "data"

from corpus.tools import load_from_corpus, read_corpus_file  # noqa: E402

setup()
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

VMWARE_SCHEMA.config.generation.update(max_examples=1)
VMWARE_SCHEMA.config.seed = 42
VMWARE_SCHEMA.config.phases.update(phases=["examples", "fuzzing"])

BBCI_SCHEMA.config.generation.update(max_examples=1)
BBCI_SCHEMA.config.seed = 42
BBCI_SCHEMA.config.phases.update(phases=["examples", "fuzzing"])


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
def test_iter_operations(benchmark, raw_schema, loader):
    schema = loader(raw_schema, config=CONFIG)

    def _iter_operations():
        for _ in schema.get_all_operations():
            pass

    benchmark(_iter_operations)


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "raw_schema, loader",
    [
        (VMWARE, schemathesis.openapi.from_dict),
        (STRIPE, schemathesis.openapi.from_dict),
        (UNIVERSE, schemathesis.graphql.from_dict),
        (APPVEYOR, schemathesis.openapi.from_dict),
        (EVETECH, schemathesis.openapi.from_dict),
        (OSISOFT, schemathesis.openapi.from_dict),
        (ML_WEBSERVICES, schemathesis.openapi.from_dict),
        (AZURE_NETWORK, schemathesis.openapi.from_dict),
    ],
    ids=("vmware", "stripe", "universe", "appveyor", "evetech", "osisoft", "ml_webservices", "azure_network"),
)
def test_measure_statistic(benchmark, raw_schema, loader):
    schema = loader(raw_schema, config=CONFIG)
    benchmark(schema._measure_statistic)


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "schema, key",
    [
        (BBCI_SCHEMA, ("/categories", "get")),
        (VMWARE_SCHEMA, ("/entities/problems", "get")),
        (UNIVERSE_SCHEMA, ("Query", "manageTickets")),
    ],
    ids=("bbci", "vmware", "universe"),
)
def test_get_operation(benchmark, schema, key):
    def _get():
        current = schema
        for segment in key:
            current = current[segment]

    benchmark(_get)


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "schema, key",
    [
        (BBCI_SCHEMA, "Get_Categories_"),
        (VMWARE_SCHEMA, "listProblemEvents"),
    ],
    ids=("bbci", "vmware"),
)
def test_find_operation_by_id(benchmark, schema, key):
    benchmark(schema.find_operation_by_id, key)


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "schema, key",
    [
        (BBCI_SCHEMA, "#/paths/~1categories/get"),
        (VMWARE_SCHEMA, "#/paths/~1entities~1problems/get"),
    ],
    ids=("bbci", "vmware"),
)
def test_find_operation_by_reference(benchmark, schema, key):
    benchmark(schema.find_operation_by_reference, key)


def _optimized_schema(operations):
    for operation in operations:
        for parameter in operation.ok().iter_parameters():
            _ = parameter.optimized_schema


@pytest.mark.benchmark
@pytest.mark.parametrize("operations", [BBCI_OPERATIONS, VMWARE_OPERATIONS], ids=("bbci", "vmware"))
def test_as_json_schema(operations, benchmark):
    benchmark(_optimized_schema, operations)


def _get_parameters_strategy(operations, config):
    for operation in operations:
        for location in [
            ParameterLocation.HEADER,
            ParameterLocation.COOKIE,
            ParameterLocation.PATH,
            ParameterLocation.QUERY,
        ]:
            get_parameters_strategy(operation.ok(), GenerationMode.POSITIVE, location, config)


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "operations, config",
    [
        (BBCI_OPERATIONS, BBCI_SCHEMA.config.generation),
        (VMWARE_OPERATIONS, VMWARE_SCHEMA.config.generation),
    ],
    ids=("bbci", "vmware"),
)
def test_get_parameters_strategy(benchmark, operations, config):
    benchmark(_get_parameters_strategy, operations, config)


@pytest.mark.benchmark
def test_events(benchmark):
    def _events_run():
        engine = from_schema(BBCI_SCHEMA)
        for _ in engine.execute():
            pass

    benchmark(_events_run)


def _write_vcr(entries, config):
    queue = Queue()
    for entry in entries:
        queue.put(entry)

    vcr_writer(StringIO(), config, queue)


def _write_har(entries, config):
    queue = Queue()
    for entry in entries:
        queue.put(entry)

    har_writer(StringIO(), config, queue)


def _collect_cassette_entries(schema):
    engine = from_schema(schema)
    entries = [Initialize(seed=schema.config.seed)]
    entries.extend(
        Process(recorder=event.recorder) for event in engine.execute() if isinstance(event, events.ScenarioFinished)
    )
    entries.append(Finalize())
    return entries


@pytest.mark.parametrize("schema", [VMWARE_SCHEMA], ids=("vmware",))
@pytest.mark.benchmark
def test_vcr(benchmark, schema):
    entries = _collect_cassette_entries(schema)
    benchmark(_write_vcr, entries, schema.config)


@pytest.mark.parametrize("schema", [BBCI_SCHEMA, VMWARE_SCHEMA], ids=("bbci", "vmware"))
@pytest.mark.benchmark
def test_har(benchmark, schema):
    entries = _collect_cassette_entries(schema)
    benchmark(_write_har, entries, schema.config)


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
def test_deepclone(benchmark, schema):
    benchmark(deepclone, schema)


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "schema",
    [
        BBCI_SCHEMA,
        VMWARE_SCHEMA,
        STRIPE_SCHEMA,
    ],
    ids=("bbci", "vmware", "stripe"),
)
def test_dependency_analysis(benchmark, schema):
    benchmark(dependencies.analyze, schema)


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "schema",
    [
        VMWARE_SCHEMA,
        STRIPE_SCHEMA,
    ],
    ids=("vmware", "stripe"),
)
def test_link_generation(benchmark, schema):
    graph = dependencies.analyze(schema)
    benchmark(lambda: list(graph.iter_links()))


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "schema",
    [
        BBCI_SCHEMA,
        VMWARE_SCHEMA,
        STRIPE_SCHEMA,
    ],
    ids=("bbci", "vmware", "stripe"),
)
def test_dependency_layers(benchmark, schema):
    graph = dependencies.analyze(schema)
    benchmark(compute_dependency_layers, graph)


def _load_from_file(loader, json_string):
    return loader(json_string, config=CONFIG)


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "raw_schema, loader",
    [
        (BBCI, schemathesis.openapi.from_file),
        (VMWARE, schemathesis.openapi.from_file),
        (STRIPE, schemathesis.openapi.from_file),
        (UNIVERSE, schemathesis.graphql.from_file),
        (APPVEYOR, schemathesis.openapi.from_file),
        (EVETECH, schemathesis.openapi.from_file),
        (OSISOFT, schemathesis.openapi.from_file),
        (ML_WEBSERVICES, schemathesis.openapi.from_file),
        (AZURE_NETWORK, schemathesis.openapi.from_file),
    ],
    ids=("bbci", "vmware", "stripe", "universe", "appveyor", "evetech", "osisoft", "ml_webservices", "azure_network"),
)
def test_load_from_file(benchmark, raw_schema, loader):
    serialized = json.dumps(raw_schema)
    benchmark(_load_from_file, loader, serialized)


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "raw_schema",
    [
        BBCI,
        VMWARE,
        STRIPE,
    ],
    ids=("bbci", "vmware", "stripe"),
)
def test_as_state_machine(benchmark, raw_schema):
    def _build():
        schema = schemathesis.openapi.from_dict(deepclone(raw_schema))
        schema.as_state_machine()

    benchmark(_build)
