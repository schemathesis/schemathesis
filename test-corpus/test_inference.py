import json
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


def clean_schema(obj):
    # A helper to display schemas without fields that make too much noise and are irrelevant to dependency analysis
    if isinstance(obj, dict):
        return {k: clean_schema(v) for k, v in obj.items() if k not in ("description", "title", "summary")}
    elif isinstance(obj, list):
        return [clean_schema(item) for item in obj]
    else:
        return obj


def save_schema(schema, filename="schema.json"):
    with open(filename, "w") as fd:
        json.dump(clean_schema(schema), fd, indent=4)


KNOWN_FIELDLESS_RESOURCES = {
    "pandascore.co/2.23.1.json": frozenset(
        [
            # Not supported yet: contain multiple resources behind `oneOf`
            "NonDeletionIncident",
            "Incident",
            "Standing",
            "Videogame",
        ]
    ),
    "ably.io/platform/1.1.0.json": frozenset(
        [
            # This one has an empty schema
            "Stat",
            # This is an array of numbers
            "Time",
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
    "collegefootballdata.com/4.4.12.json": frozenset(
        [
            # A simple string
            "Category",
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


def test_overall_metrics(snapshot_json):
    total_resources = 0
    total_inputs = 0
    total_outputs = 0
    total_links = 0
    for corpus in CORPUS_FILES.values():
        for member in corpus.getmembers():
            raw_content = corpus.extractfile(member).read()
            raw_schema = json_loads(raw_content)
            schema = schemathesis.openapi.from_dict(raw_schema)
            graph = dependencies.analyze(schema)
            total_resources += len(graph.resources)
            for operation in graph.operations.values():
                total_inputs += len(operation.inputs)
                total_outputs += len(operation.outputs)
            for response_links in graph.iter_links():
                total_links += len(response_links.links)
    assert {
        "total_resources": total_resources,
        "total_inputs": total_inputs,
        "total_outputs": total_outputs,
        "total_links": total_links,
    } == snapshot_json
