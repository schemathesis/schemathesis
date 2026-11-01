import base64
import ipaddress
import math
import re
import sys

import pytest
from hypothesis import HealthCheck, find, given, settings

import schemathesis
from schemathesis.config import SchemathesisWarning
from schemathesis.core.control import SkipTest
from schemathesis.core.error_feedback import ErrorFeedbackStore, Observation, ObservationKind
from schemathesis.core.errors import IncorrectUsage
from schemathesis.core.jsonschema.numeric import FLOAT32_MAX, next_float32
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.generation.feedback import FeedbackSources
from schemathesis.generation.hypothesis.examples import generate_many
from schemathesis.specs.grpc.converter import request_message
from schemathesis.specs.grpc.formats import grpc_formats
from test.apps.catalog.grpc.descriptors import (
    all_types_descriptor_set,
    load_descriptor_set,
    nested_descriptor_set,
    validated_descriptor_set,
    write_descriptor_set,
)
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


@pytest.mark.parametrize("argument", ["query", "cookies", "path_parameters"], ids=["query", "cookies", "path"])
def test_unsupported_override_rejected(schema, argument):
    with pytest.raises(IncorrectUsage, match=f"`{argument}` is not supported for gRPC operations"):
        schema["types.Types"]["Edition"].as_strategy(**{argument: {"x": "1"}})


def test_hash_ignores_case_id(schema):
    operation = schema["types.Types"]["Edition"]
    assert hash(operation.Case(body={"id": 1}, headers={"authorization": "Bearer x"})) == hash(
        operation.Case(body={"id": 1}, headers={"authorization": "Bearer x"})
    )


@pytest.mark.parametrize(
    ("method", "body", "headers"),
    [
        ("Edition", {"id": 2}, {"authorization": "Bearer x"}),
        ("Edition", {"id": 1}, {"authorization": "Bearer y"}),
        ("Edition", b"\x08\x03", {"authorization": "Bearer x"}),
        ("Legacy", {"id": 1, "blob": {}, "label": ""}, {"authorization": "Bearer x"}),
    ],
    ids=["body", "unmasked-header", "raw-bytes", "method"],
)
def test_hash_tells_cases_apart(schema, method, body, headers):
    case = schema["types.Types"]["Edition"].Case(body={"id": 1}, headers={"authorization": "Bearer x"})
    assert hash(case) != hash(schema["types.Types"][method].Case(body=body, headers=headers))


def test_hash_ignores_map_entry_order(schema):
    operation = schema["types.Types"]["Maps"]
    assert hash(operation.Case(body={"byString": {"a": "1", "b": "2"}})) == hash(
        operation.Case(body={"byString": {"b": "2", "a": "1"}})
    )


@pytest.mark.parametrize(
    ("body", "equal_body"),
    [
        ({"id": 1, "unknown": 1}, {"unknown": 1, "id": 1}),
        ({"unknown": {"b": 1, "a": [1]}}, {"unknown": {"a": [1], "b": 1}}),
        (7, 7),
    ],
    ids=["unknown-field", "nested-unknown-field", "not-an-object"],
)
def test_hash_of_unencodable_body_matches_equal_case(schema, body, equal_body):
    operation = schema["types.Types"]["Edition"]
    assert hash(operation.Case(body=body)) == hash(operation.Case(body=equal_body))


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


def span_rules(span):
    target = span.WhichOneof("target")
    return {
        "trace_id": len(span.trace_id) == 16,
        "span_id": len(span.span_id) == 8,
        "payload": 2 <= len(span.payload) <= 7,
        "upload": 1 <= len(span.upload) <= 1048576,
        "preview": len(span.preview) <= 1048576,
        "inverted": span.inverted == b"",
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


def extras_rules(extras):
    return {
        "file": 8 <= len(extras.file.encode()) <= 40
        and extras.file.endswith(".txt")
        and "a.b" in extras.file
        and "zz" not in extras.file,
        "slug": re.fullmatch("ab[a-z]*", extras.slug) is not None,
        "fixed": extras.fixed == "x",
        "chosen": extras.chosen in ("a", "b"),
        "short": len(extras.short) == 3,
        "digest": 4 <= len(extras.digest.encode()) <= 8,
        "word": re.fullmatch("[A-Za-z]+[^0-9]", extras.word) is not None,
        "version": re.fullmatch(r"v1\.0(\.[0-9]+)?", extras.version) is not None,
        "day": extras.day[:3].lower() == "mon" or extras.day[-3:].lower() == "tue",
        "spaced": re.fullmatch("[A-Za-z0-9_\t\n\f\r ]+[\t\n\f\r ][^\t\n\f\r ]", extras.spaced) is not None,
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


def edges_rules(edges):
    return {
        "small": 0 < edges.small < 1,
        "nonzero": edges.nonzero != 0,
        "token": edges.token in (b"\x00\x01", b"\x01\x02"),
        "magic": edges.magic == b"\xfb\xff",
        "only": edges.WhichOneof("pick") == "only",
        "ranked": set(edges.ranked) <= {5, 7},
        "below": set(edges.below) <= {-2, -1},
        "picks": edges.picks in (1, 3),
        "triple": len(edges.triple) >= 3,
        "shade": edges.shade != 0,
        "shades": len(set(edges.shades)) == len(edges.shades),
        "odds": edges.odds not in (0.5, 1.100000023841858, 1.5, FLOAT32_MAX, math.inf, -math.inf),
        "pinned": set(edges.pinned) <= {5},
        "outer": all(key > 10 or key < 5 for key in edges.outer),
        "created": edges.HasField("created"),
    }


def choices_rules(choices):
    return {
        "label": choices.WhichOneof("boxed") == "label",
        "matcher": choices.WhichOneof("matcher") is not None,
        "names": (choices.short_name == "") != (choices.alias == "")
        and (choices.short_name == "" or len(choices.short_name) >= 2),
    }


RULES = {"Record": span_rules, "Check": extras_rules, "Edge": edges_rules, "Choose": choices_rules}


@pytest.fixture
def validated_schema(tmp_path):
    return load_descriptor_set(tmp_path, validated_descriptor_set())


def broken_rules(operation, body):
    checks = RULES[operation.definition.raw.name](request_message(body, operation.definition.raw.input_type))
    return [name for name, holds in checks.items() if not holds]


@pytest.mark.parametrize("method", RULES)
def test_positive_values_satisfy_validation_rules(validated_schema, method):
    operation = validated_schema["validated.Spans"][method]

    @run_given(case=operation.as_strategy(), max_examples=50)
    def test(case):
        assert broken_rules(operation, case.body) == [], case.body


def test_positive_values_satisfy_validation_rules_with_reported_fields(validated_schema):
    operation = validated_schema["validated.Spans"]["Edge"]
    feedback = feedback_for(operation, ("nonzero",), ("picks",), ("triple",))

    @run_given(case=operation.as_strategy(feedback=feedback), max_examples=50)
    def test(case):
        assert broken_rules(operation, case.body) == [], case.body


# `find` raises when no generated body sets another member of the oneof.
def test_legacy_rules_of_oneof_member_apply_only_when_set(validated_schema):
    find(validated_schema["validated.Spans"]["Choose"].as_strategy(), lambda case: "exact" in case.body)


# Newer Pythons drop standard-alphabet characters from URL-safe base64 and warn about it first.
@pytest.mark.filterwarnings("error::FutureWarning")
@pytest.mark.parametrize("method", RULES)
def test_coverage_cases_satisfy_validation_rules(validated_schema, method):
    operation = validated_schema["validated.Spans"][method]
    cases = validated_schema.iter_coverage_cases(
        operation, generation_modes=[GenerationMode.POSITIVE], generation_config=validated_schema.config.generation
    )
    assert [
        (case.meta.phase.data.description, broken_rules(operation, case.body))
        for case in cases
        if broken_rules(operation, case.body)
    ] == []


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


def coverage_cases(schema, method, service="types.Types"):
    operation = schema[service][method]
    return [
        (case.meta.phase.data.description, case.body)
        for case in schema.iter_coverage_cases(
            operation, generation_modes=[GenerationMode.POSITIVE], generation_config=schema.config.generation
        )
    ]


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        (
            "Envelope",
            [
                ("Default positive test case", {}),
                ("Every nested message empty", {"first": {}, "second": {}}),
                ("Empty `first` message", {"first": {}}),
                ("Empty `second` message", {"second": {}}),
            ],
        ),
        (
            "Choice",
            [
                ("Default positive test case", {}),
                ("Oneof branch `email`", {"email": ""}),
                ("Oneof branch `node`", {"node": {}}),
                ("Empty `note`", {"note": ""}),
                ("One default item in `node.children`", {"node": {"children": [{}]}}),
                ("One default item in `node.children[0].children`", {"node": {"children": [{"children": [{}]}]}}),
            ],
        ),
        (
            "Legacy",
            [
                ("Default positive test case", {"id": 0, "blob": {}, "label": ""}),
                ("Minimum `id`", {"id": -2147483648, "blob": {}, "label": ""}),
                ("`level` set to `LOW`", {"id": 0, "blob": {}, "label": "", "level": "LOW"}),
                ("Maximum `id`", {"id": 2147483647, "blob": {}, "label": ""}),
            ],
        ),
        (
            "Edition",
            [
                ("Default positive test case", {"id": 0}),
                ("Minimum `id`", {"id": -2147483648}),
                ("Empty `name`", {"id": 0, "name": ""}),
                ("Maximum `id`", {"id": 2147483647}),
            ],
        ),
    ],
)
def test_coverage_cases(schema, method, expected):
    assert coverage_cases(schema, method) == expected


def test_coverage_case_counts(schema):
    assert {method: len(coverage_cases(schema, method)) for method in METHODS} == {
        "Scalars": 26,
        "Maps": 7,
        "WellKnown": 35,
        "Node": 4,
        "Envelope": 4,
        "Choice": 6,
        "Legacy": 4,
        "Edition": 4,
    }


def test_coverage_scalar_bounds(schema):
    cases = dict(coverage_cases(schema, "Scalars"))
    assert (
        cases["Minimum `int64`"],
        cases["Maximum `uint64`"],
        cases["One-byte `bytes`"],
        cases["Unknown `color` number 2"],
        cases["`color` set to `RED`"],
        cases["`bool` set to true"],
        cases["Minimum `float`"],
        cases["Maximum `double`"],
        cases["One default item in `tags`"],
    ) == (
        {"int64": -(2**63)},
        {"uint64": 2**64 - 1},
        {"bytes": "AA=="},
        {"color": 2},
        {"color": "RED"},
        {"bool": True},
        {"float": -FLOAT32_MAX},
        {"double": sys.float_info.max},
        {"tags": [0]},
    )


def test_coverage_exclusive_float_bounds(validated_schema):
    span = dict(coverage_cases(validated_schema, "Record", "validated.Spans"))
    edges = dict(coverage_cases(validated_schema, "Edge", "validated.Spans"))
    assert (span["Maximum `ratio`"]["ratio"], edges["Maximum `small`"]["small"]) == (
        math.nextafter(1, 0),
        next_float32(1, going_up=False),
    )


@pytest.fixture
def nested_schema(tmp_path):
    return load_descriptor_set(tmp_path, nested_descriptor_set())


def test_coverage_nested_messages(nested_schema):
    assert coverage_cases(nested_schema, "Chain", "nested.Nested") == [
        ("Default positive test case", {}),
        ("Every nested message empty", {"middle": {"leaf": {}, "other": {}}}),
        ("Empty `middle` message", {"middle": {}}),
        ("Empty `middle.leaf` message", {"middle": {"leaf": {}}}),
        ("Empty `middle.other` message", {"middle": {"other": {}}}),
        ("One default item in `middle.leaf.kinds`", {"middle": {"leaf": {"kinds": ["KIND_UNSPECIFIED"]}}}),
        ("One default item in `middle.other.kinds`", {"middle": {"other": {"kinds": ["KIND_UNSPECIFIED"]}}}),
    ]


def test_coverage_cases_are_capped(nested_schema):
    cases = coverage_cases(nested_schema, "Wide", "nested.Nested")
    assert (len(cases), cases[-1][0]) == (100, "One default item in `leaf38.kinds`")


def test_coverage_cap_reaches_fields_after_many_bounds(nested_schema):
    cases = coverage_cases(nested_schema, "Flat", "nested.Nested")
    assert (len(cases), [description for description, _ in cases if "`child" in description]) == (
        100,
        ["One default item in `child.kinds`"],
    )


def test_coverage_keeps_self_nested_messages_shallow(nested_schema):
    cases = dict(coverage_cases(nested_schema, "Tree", "nested.Nested"))
    assert cases["Every nested message empty"] == {f"branch{number}": {} for number in range(1, 31)}


def test_coverage_unknown_enum_number_fits_int32(nested_schema):
    assert coverage_cases(nested_schema, "Edge", "nested.Nested") == [
        ("Default positive test case", {}),
        ("`extreme` set to `EXTREME_MAX`", {"extreme": "EXTREME_MAX"}),
        ("Unknown `extreme` number 1", {"extreme": 1}),
    ]


def containers(value):
    if isinstance(value, (dict, list)):
        yield value
        for item in value.values() if isinstance(value, dict) else value:
            yield from containers(item)


def test_coverage_case_bodies_share_no_values(schema):
    operation = schema["types.Types"]["WellKnown"]
    cases = list(
        schema.iter_coverage_cases(
            operation, generation_modes=[GenerationMode.POSITIVE], generation_config=schema.config.generation
        )
    )
    identities = [id(value) for case in cases for value in containers(case.body)]
    assert len(identities) == len(set(identities))


def test_coverage_without_positive_mode(schema):
    operation = schema["types.Types"]["Scalars"]
    assert (
        list(
            schema.iter_coverage_cases(
                operation, generation_modes=[GenerationMode.NEGATIVE], generation_config=schema.config.generation
            )
        )
        == []
    )


# Command-line clients reject the URL-safe alphabet in JSON bodies.
def test_bytes_use_standard_alphabet():
    @run_given(grpc_formats()["byte"], max_examples=50)
    def test(value):
        assert base64.b64encode(base64.b64decode(value, validate=True)).decode() == value


@pytest.mark.parametrize("method", METHODS)
def test_every_positive_value_encodes(schema, method):
    operation = schema["types.Types"][method]

    # Bounds and map keys are rare draws.
    @run_given(case=operation.as_strategy(), max_examples=100)
    def test(case):
        request_message(case.body, operation.definition.raw.input_type).SerializeToString()


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


def feedback_for(operation, *paths, kind=ObservationKind.MUST_NOT_BE_BLANK):
    store = ErrorFeedbackStore()
    for path in paths:
        store.record(
            Observation(
                operation_label=operation.label,
                location=ParameterLocation.BODY,
                parameter_path=path,
                kind=kind,
                raw_message="must not be empty",
            )
        )
    return FeedbackSources(error_feedback=store)


def is_set(message, name):
    field = message.DESCRIPTOR.fields_by_name[name]
    value = getattr(message, name)
    if field.is_repeated:
        return len(value) > 0
    if field.has_presence:
        return message.HasField(name)
    return value != field.default_value


@pytest.mark.parametrize(
    ("method", "reported", "names"),
    [
        ("Scalars", ("int32",), ["int32"]),
        ("Scalars", ("double",), ["double"]),
        ("Scalars", ("float",), ["float"]),
        ("Scalars", ("bool",), ["bool"]),
        ("Scalars", ("string",), ["string"]),
        ("Scalars", ("bytes",), ["bytes"]),
        ("Scalars", ("color",), ["color"]),
        ("Scalars", ("tags",), ["tags"]),
        ("Maps", ("byString",), ["by_string"]),
        ("WellKnown", ("int32Value",), ["int32_value"]),
        ("WellKnown", ("timestamp", "seconds"), ["timestamp"]),
        ("Choice", ("note",), ["note"]),
        ("Envelope", ("first",), ["first"]),
        ("Envelope", ("payload",), []),
        ("Node", ("name",), ["name"]),
        ("Node", ("children",), ["children"]),
        ("Envelope", ("first", "title"), ["first"]),
        ("StringValue", ("value",), []),
        ("Legacy", ("level",), ["level"]),
    ],
    ids=lambda value: ".".join(value) if isinstance(value, tuple) else None,
)
def test_reported_missing_fields_are_always_set(schema, method, reported, names):
    operation = schema["types.Types"][method]

    @run_given(case=operation.as_strategy(feedback=feedback_for(operation, reported)), max_examples=10)
    def test(case):
        decoded = request_message(case.body, operation.definition.raw.input_type)
        assert [name for name in names if not is_set(decoded, name)] == [], case.body


def test_reported_nested_fields_are_always_set(schema):
    operation = schema["types.Types"]["Envelope"]
    feedback = feedback_for(operation, ("first", "extra"), ("first", "title"), ("second",))

    @run_given(case=operation.as_strategy(feedback=feedback), max_examples=10)
    def test(case):
        assert (case.body["first"]["title"] != "", "second" in case.body) == (True, True), case.body


def test_reported_members_of_one_oneof_set_exactly_one(schema):
    operation = schema["types.Types"]["Choice"]
    input_type = operation.definition.raw.input_type

    @run_given(case=operation.as_strategy(feedback=feedback_for(operation, ("email",), ("node",))), max_examples=10)
    def test(case):
        assert request_message(case.body, input_type).WhichOneof("target") in ("email", "node"), case.body


def leaves_unset(body, path):
    *parents, last = path
    for name in parents:
        try:
            body = body[name]
        except (KeyError, IndexError):
            return False
    return not body.get(last)


# `find` raises when every generated body sets the field.
@pytest.mark.parametrize(
    ("method", "kind", "reported", "unset"),
    [
        ("Envelope", ObservationKind.MUST_NOT_BE_BLANK, ("first", "title"), ("second", "title")),
        ("Node", ObservationKind.MUST_NOT_BE_BLANK, ("children",), ("children", 0, "children")),
        ("Scalars", ObservationKind.FORMAT, ("string",), ("string",)),
    ],
    ids=["same-type-elsewhere", "recursive", "other-kind"],
)
def test_fields_outside_reported_paths_stay_optional(schema, method, kind, reported, unset):
    operation = schema["types.Types"][method]
    strategy = operation.as_strategy(feedback=feedback_for(operation, reported, kind=kind))
    find(strategy, lambda case: leaves_unset(case.body, unset))
