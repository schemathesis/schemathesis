import concurrent.futures
import multiprocessing
import pathlib
import sys

import pytest
from syrupy.extensions.json import JSONSnapshotExtension

import schemathesis
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


@pytest.fixture
def snapshot_json(snapshot):
    return snapshot.with_defaults(extension_class=JSONSnapshotExtension)


SLOW = {
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

KNOWN_FIELDLESS_RESOURCES = {
    "pandascore.co/2.23.1.json": frozenset(
        [
            # Not supported yet: contain multiple resources behind `oneOf`
            "NonDeletionIncident",
            "Incident",
            "Standing",
            "Videogame",
            "LeagueVideogame",
            "CurrentVideogame",
        ]
    ),
    "ably.io/platform/1.1.0.json": frozenset(
        [
            # This one has an empty schema
            "Stat",
        ]
    ),
    "amazonaws.com/entityresolution/2018-05-10.json": frozenset(
        [
            # Empty objects
            "TagMap",
            "TagResourceOutput",
            "UntagResourceOutput",
        ]
    ),
    "digitallocker.gov.in/authpartner/1.0.0.json": frozenset(
        [
            # Response is empty, it is ok. But requestBody has the full definition
            "Pushuri",
        ]
    ),
    "googleapis.com/baremetalsolution/v1.json": frozenset(
        [
            # Indeed an empty schema
            "Empty",
        ]
    ),
    "redhat.local/patchman-engine/v1.15.3.json": frozenset(
        [
            # Only custom additional fields
            "controllers.AdvisoriesSystemsResponse",
            "controllers.SystemsAdvisoriesResponse",
        ]
    ),
}

KNOWN_INCORRECT_FIELD_MAPPINGS = {}


@pytest.mark.parametrize(
    ["corpus", "filename"],
    [
        ("openapi-3.0", "ably.io/platform/1.1.0.json"),
        ("openapi-3.0", "amazonaws.com/entityresolution/2018-05-10.json"),
        ("swagger-2.0", "azure.com/containerinstance-containerInstance/2017-10-01-preview.json"),
        ("openapi-3.0", "collegefootballdata.com/4.4.12.json"),
        ("openapi-3.0", "digitallocker.gov.in/authpartner/1.0.0.json"),
        ("openapi-3.0", "googleapis.com/baremetalsolution/v1.json"),
        ("swagger-2.0", "mashape.com/geodb/1.0.0.json"),
        ("swagger-2.0", "mastercard.com/Locations/1.0.0.json"),
        ("openapi-3.0", "pandascore.co/2.23.1.json"),
        ("openapi-3.0", "redhat.local/patchman-engine/v1.15.3.json"),
        ("openapi-3.0", "twilio.com/twilio_routes_v2/1.55.0.json"),
        ("openapi-3.0", "wealthreader.com/1.0.0.json"),
        ("openapi-3.0", "twilio.com/api/1.55.0.json"),
    ],
)
def test_dependency_graph(corpus, filename, snapshot_json):
    # Corpus is sampled due to its size
    raw_content = CORPUS_FILES[corpus].extractfile(filename).read()
    raw_schema = json_loads(raw_content)
    schema = schemathesis.openapi.from_dict(raw_schema)
    graph = dependencies.analyze(schema)
    serialized = graph.serialize()

    graph.assert_fieldless_resources(filename, KNOWN_FIELDLESS_RESOURCES)
    graph.assert_incorrect_field_mappings(filename, KNOWN_INCORRECT_FIELD_MAPPINGS)

    assert serialized == snapshot_json


def _process_member(raw_content):
    raw_schema = json_loads(raw_content)
    schema = schemathesis.openapi.from_dict(raw_schema)
    graph = dependencies.analyze(schema)

    resources = len(graph.resources)
    inputs = 0
    outputs = 0
    links = 0

    for operation in graph.operations.values():
        inputs += len(operation.inputs)
        outputs += len(operation.outputs)
    for response_links in graph.iter_links():
        links += len(response_links.links)

    return resources, inputs, outputs, links


def test_overall_metrics(snapshot_json):
    work_items = []
    for corpus in CORPUS_FILES.values():
        for member in corpus.getmembers():
            if member.name in SLOW:
                continue
            raw_content = corpus.extractfile(member).read()
            work_items.append(raw_content)

    max_workers = multiprocessing.cpu_count()
    total_resources = total_inputs = total_outputs = total_links = 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as exe:
        for resources, inputs, outputs, links in exe.map(_process_member, work_items, chunksize=16):
            total_resources += resources
            total_inputs += inputs
            total_outputs += outputs
            total_links += links

    assert {
        "total_resources": total_resources,
        "total_inputs": total_inputs,
        "total_outputs": total_outputs,
        "total_links": total_links,
    } == snapshot_json
