import base64
import ipaddress
import re

import pytest
from google.protobuf import json_format, message_factory
from hypothesis import HealthCheck, given, settings

import schemathesis
from schemathesis.config import SchemathesisWarning
from schemathesis.core.control import SkipTest
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis.examples import generate_many
from schemathesis.specs.grpc.formats import grpc_formats
from test.apps.catalog.grpc.descriptors import all_types_descriptor_set, validated_descriptor_set, write_descriptor_set
from test.utils import run_given

# The address and host checks the validation rules define.
EMAIL = re.compile(
    r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*"
)
HOSTNAME = re.compile(r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9-]*[a-zA-Z][a-zA-Z0-9-]*")
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
METHODS = ["Scalars", "Maps", "WellKnown", "Node", "Envelope", "Choice", "Legacy", "Edition"]


@pytest.fixture(scope="module")
def path(tmp_path_factory):
    return write_descriptor_set(tmp_path_factory.mktemp("grpc"), all_types_descriptor_set())


@pytest.fixture
def schema(path):
    return schemathesis.grpc.from_path(path)


def test_case_shape(schema):
    case = generate_many(schema["types.Types"]["Legacy"].as_strategy(), 1)[0]
    assert (
        case.method,
        case.path,
        case.media_type,
        case.body,
        case.headers,
        case.query,
        case.cookies,
        case.path_parameters,
        case.meta.generation.mode,
    ) == (
        "POST",
        "/types.Types/Legacy",
        "application/grpc",
        {"id": 0, "blob": {}, "label": ""},
        {},
        {},
        {},
        {},
        GenerationMode.POSITIVE,
    )


def test_explicit_body_and_headers(schema):
    strategy = schema["types.Types"]["Edition"].as_strategy(body={"id": 1}, headers={"authorization": "Bearer x"})
    case = generate_many(strategy, 1)[0]
    assert (case.body, case.headers) == ({"id": 1}, {"authorization": "Bearer x"})


def test_body_hooks_apply(schema):
    @schema.hook
    def map_body(context, body):
        return {"id": 7}

    case = generate_many(schema["types.Types"]["Edition"].as_strategy(), 1)[0]
    assert case.body == {"id": 7}


def is_ip(value):
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def test_positive_values_satisfy_validation_rules(tmp_path):
    schema = schemathesis.grpc.from_path(write_descriptor_set(tmp_path, validated_descriptor_set()))
    operation = schema["validated.Spans"]["Record"]
    message = message_factory.GetMessageClass(operation.definition.raw.input_type)

    @run_given(case=operation.as_strategy(), max_examples=50)
    def test(case):
        span = json_format.ParseDict(case.body, message())
        target = span.WhichOneof("target")
        checks = {
            "trace_id": len(span.trace_id) == 16,
            "span_id": len(span.span_id) == 8,
            "payload": 2 <= len(span.payload) <= 7,
            "id": UUID.fullmatch(span.id) is not None,
            "name": 4 <= len(span.name) <= 10 and span.name.startswith("sp-") and span.name != "sp-x",
            "email": EMAIL.fullmatch(span.email) is not None,
            "count": 0 < span.count <= 100,
            "total": span.total >= 1 and span.total != 13,
            "ratio": 0 < span.ratio < 1,
            "outside": span.outside > 10 or span.outside < 5,
            "kind": span.kind in (1, 2),
            "tags": 1 <= len(span.tags) <= 3
            and len(set(span.tags)) == len(span.tags)
            and all(len(tag) >= 2 for tag in span.tags),
            "labels": 1 <= len(span.labels) <= 2 and all(key and len(value) <= 5 for key, value in span.labels.items()),
            "parent": span.HasField("parent"),
            "note": span.note == "" or len(span.note) >= 3,
            "code": span.code != "",
            "nickname": span.HasField("nickname") and len(span.nickname.value) <= 3,
            "target": (target == "host" and HOSTNAME.fullmatch(span.host) is not None)
            or (target == "address" and is_ip(span.address)),
            "side": span.WhichOneof("side") is not None,
            "legacy": len(span.legacy) <= 4,
            "legacy_parent": span.HasField("legacy_parent"),
        }
        assert checks == dict.fromkeys(checks, True), case.body


def test_positive_values_satisfy_more_validation_rules(tmp_path):
    schema = schemathesis.grpc.from_path(write_descriptor_set(tmp_path, validated_descriptor_set()))
    operation = schema["validated.Spans"]["Check"]
    message = message_factory.GetMessageClass(operation.definition.raw.input_type)

    @run_given(case=operation.as_strategy(), max_examples=50)
    def test(case):
        extras = json_format.ParseDict(case.body, message())
        checks = {
            "file": 8 <= len(extras.file.encode()) <= 40
            and extras.file.endswith(".txt")
            and "a.b" in extras.file
            and "zz" not in extras.file,
            "slug": re.fullmatch("ab[a-z]*", extras.slug) is not None,
            "fixed": extras.fixed == "x",
            "chosen": extras.chosen in ("a", "b"),
            "short": len(extras.short) == 3,
            "v4": isinstance(ipaddress.ip_address(extras.v4), ipaddress.IPv4Address),
            "v6": isinstance(ipaddress.ip_address(extras.v6), ipaddress.IPv6Address),
            "link": extras.link.startswith("https://") and HOSTNAME.fullmatch(extras.link[8:]) is not None,
            "ref": extras.ref != "",
            "trimmed": re.fullmatch("[0-9a-fA-F]{32}", extras.trimmed) is not None,
            "place": HOSTNAME.fullmatch(extras.place) is not None or is_ip(extras.place),
            "level": extras.level in (1, 2),
            "scale": extras.scale == 1.5,
            "on": extras.on,
            "flag": extras.flag,
            "amount": extras.amount != 0,
            "picked": extras.picked != 0,
            "mode": extras.mode == 2,
            "modes": all(mode in (1, 2) for mode in extras.modes),
            "blob": len(extras.blob) >= 3,
            "counts": len(extras.counts) >= 1 and all(key > 0 for key in extras.counts),
            "names": 1 <= len(extras.names) <= 2,
            "only": extras.WhichOneof("only") == "solo",
        }
        assert checks == dict.fromkeys(checks, True), case.body


def test_positive_values_satisfy_validation_rules_on_edge_cases(tmp_path):
    schema = schemathesis.grpc.from_path(write_descriptor_set(tmp_path, validated_descriptor_set()))
    operation = schema["validated.Spans"]["Edge"]
    message = message_factory.GetMessageClass(operation.definition.raw.input_type)

    @run_given(case=operation.as_strategy(), max_examples=50)
    def test(case):
        edges = json_format.ParseDict(case.body, message())
        checks = {
            "small": 0 < edges.small < 1,
            "nonzero": edges.nonzero != 0,
            "token": edges.token in (b"\x00\x01", b"\x01\x02"),
            "magic": edges.magic == b"\x01\x02",
            "only": edges.WhichOneof("pick") == "only",
            "ranked": set(edges.ranked) <= {5, 7},
            "below": set(edges.below) <= {-2, -1},
            "created": edges.HasField("created"),
        }
        assert checks == dict.fromkeys(checks, True), case.body


def test_negative_only_skips(schema):
    schema.config.generation.update(modes=[GenerationMode.NEGATIVE])
    strategy = schema["types.Types"]["Edition"].as_strategy(generation_mode=GenerationMode.NEGATIVE)

    @given(strategy)
    @settings(max_examples=1, suppress_health_check=list(HealthCheck))
    def test(case):
        pass

    with pytest.raises(SkipTest, match="Negative test cases are not available for gRPC operations"):
        test()


def test_negative_falls_back_to_positive(schema):
    @run_given(schema["types.Types"]["Edition"].as_strategy(generation_mode=GenerationMode.NEGATIVE), max_examples=1)
    def test(case):
        assert case.meta.generation.mode == GenerationMode.POSITIVE


# Command-line clients reject the URL-safe alphabet in JSON bodies.
def test_bytes_use_standard_alphabet():
    @run_given(grpc_formats()["byte"], max_examples=50)
    def test(value):
        assert base64.b64encode(base64.b64decode(value, validate=True)).decode() == value


@pytest.mark.parametrize("method", METHODS)
def test_every_positive_value_encodes(schema, method):
    operation = schema["types.Types"][method]
    message = message_factory.GetMessageClass(operation.definition.raw.input_type)

    # Bounds and map keys are rare draws.
    @run_given(case=operation.as_strategy(), max_examples=100)
    def test(case):
        json_format.ParseDict(case.body, message()).SerializeToString()


def test_any_fields_are_reported(schema):
    assert [
        (warning.kind, warning.operation_label, warning.message, warning.group)
        for warning in schema.iter_schema_warnings()
    ] == [
        (SchemathesisWarning.UNSUPPORTED_FIELD, label, f"`{field}` - `google.protobuf.Any` is not generated", None)
        for label, field in [
            ("types.Types/Envelope", "types.Envelope.payload"),
            ("types.Types/Envelope", "types.Envelope.extras"),
            ("types.Types/Envelope", "types.Meta.extra"),
            ("types.Types/Legacy", "legacy.Legacy.blob"),
        ]
    ]
