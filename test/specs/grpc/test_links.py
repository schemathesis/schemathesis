import pytest
from google.protobuf import descriptor_pb2, json_format, message_factory
from hypothesis.stateful import RULE_MARKER

import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.core.error_feedback import ErrorFeedbackStore, Observation, ObservationKind
from schemathesis.core.errors import NoLinksFound
from schemathesis.core.failures import FailureGroup, ServerError
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation.feedback import FeedbackSources
from schemathesis.generation.hypothesis.examples import generate_many
from schemathesis.generation.hypothesis.reporting import UnsatisfiableSchema
from schemathesis.specs.grpc.stateful import GrpcLink, infer_links, response_value
from test.apps.catalog.grpc.descriptors import library_descriptor_set, write_descriptor_set
from test.utils import saved_example_keys

pytest.importorskip("grpc", reason="grpcio has no free-threaded build")

NAME_LINK = GrpcLink("library.Library/CreateBook", "library.Library/GetBook", "name", "name")
PAGE_LINK = GrpcLink("library.Library/ListBooks", "library.Library/ListBooks", "next_page_token", "pageToken")
CREATE_BOOK = "library.Library/CreateBook"
GET_BOOK = "library.Library/GetBook"


def message(file, name):
    return next(item for item in file.message_type if item.name == name)


def remove_field(file, message_name, field_name):
    fields = message(file, message_name).field
    fields.remove(next(field for field in fields if field.name == field_name))


def remove_method(file, name):
    methods = file.service[0].method
    methods.remove(next(method for method in methods if method.name == name))


def make_name_numeric(file):
    message(file, "GetBookRequest").field[0].type = descriptor_pb2.FieldDescriptorProto.TYPE_INT64


def make_token_repeated(file):
    message(file, "ListBooksResponse").field[1].label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED


def return_operation(files):
    operations = files.file.add(name="operations.proto", package="google.longrunning", syntax="proto3")
    _string_field(operations.message_type.add(name="Operation"), "name", 1)
    files.file[0].dependency.append(operations.name)
    files.file[0].service[0].method[0].output_type = ".google.longrunning.Operation"


def name_in_oneof(files):
    request = message(files.file[0], "GetBookRequest")
    request.oneof_decl.add(name="identifier")
    request.field[0].oneof_index = 0
    _string_field(request, "isbn", 2).oneof_index = 0


def create_also_pages(files):
    _string_field(message(files.file[0], "CreateBookRequest"), "page_token", 2)
    _string_field(message(files.file[0], "Book"), "next_page_token", 3)


def list_filter(files):
    _string_field(message(files.file[0], "ListBooksRequest"), "filter", 3)


def make_proto2(files):
    files.file[0].syntax = "proto2"


def get_requires_version(files):
    make_proto2(files)
    message(files.file[0], "GetBookRequest").field.add(
        name="version",
        number=2,
        type=descriptor_pb2.FieldDescriptorProto.TYPE_INT32,
        label=descriptor_pb2.FieldDescriptorProto.LABEL_REQUIRED,
    )


def get_requires_itself(files):
    make_proto2(files)
    message(files.file[0], "GetBookRequest").field.add(
        name="parent",
        number=2,
        type=descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".library.GetBookRequest",
        label=descriptor_pb2.FieldDescriptorProto.LABEL_REQUIRED,
    )


def same_rule_names(files):
    # `a.b.Library` and `a_b.Library` normalize to the same rule names.
    original = files.file[0]
    for package in ("a.b", "a_b"):
        copy = files.file.add()
        copy.CopyFrom(original)
        copy.name, copy.package = f"{package}.proto", package
        for item in copy.message_type:
            for field in item.field:
                if field.type_name:
                    field.type_name = field.type_name.replace(".library.", f".{package}.")
        for method in copy.service[0].method:
            method.input_type = method.input_type.replace(".library.", f".{package}.")
            method.output_type = method.output_type.replace(".library.", f".{package}.")
    files.file.remove(original)


def _string_field(item, name, number):
    return item.field.add(
        name=name,
        number=number,
        type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
        label=descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
    )


def load(tmp_path, mutate=None, mutate_files=None, server=None):
    files = descriptor_pb2.FileDescriptorSet.FromString(library_descriptor_set())
    if mutate is not None:
        mutate(files.file[0])
    if mutate_files is not None:
        mutate_files(files)
    config = None if server is None else SchemathesisConfig.from_dict({"base-url": server.target})
    return schemathesis.grpc.from_path(write_descriptor_set(tmp_path, files.SerializeToString()), config=config)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (None, [NAME_LINK, PAGE_LINK]),
        (lambda file: remove_field(file, "Book", "name"), [PAGE_LINK]),
        (lambda file: remove_method(file, "GetBook"), [PAGE_LINK]),
        (make_name_numeric, [PAGE_LINK]),
        (lambda file: remove_field(file, "ListBooksRequest", "page_token"), [NAME_LINK]),
        (make_token_repeated, [NAME_LINK]),
    ],
    ids=["library", "resource-without-name", "no-get", "numeric-name", "no-page-token", "repeated-next-token"],
)
def test_infer_links(tmp_path, mutate, expected):
    schema = load(tmp_path, mutate)
    assert infer_links(result.ok() for result in schema.get_all_operations()) == expected


def test_long_running_create_is_not_linked(tmp_path):
    schema = load(tmp_path, mutate_files=return_operation)
    assert infer_links(result.ok() for result in schema.get_all_operations()) == [PAGE_LINK]


def test_links_follow_filters(tmp_path):
    schema = load(tmp_path).exclude(name="library.Library/GetBook")
    assert infer_links(result.ok() for result in schema.get_all_operations()) == [PAGE_LINK]


@pytest.mark.parametrize(
    ("status", "content", "mutate_files"),
    [("5", b"", None), ("0", b"\xff\xff\xff", None), ("0", b"\n\x07books/\xff", make_proto2)],
    ids=["failed-call", "undecodable-body", "proto2-name-not-utf8"],
)
def test_response_value_is_empty(tmp_path, response_factory, status, content, mutate_files):
    operation = load(tmp_path, mutate_files=mutate_files)["library.Library"]["CreateBook"]
    response = response_factory.core(content=content, headers={"grpc-status": [status]})
    assert response_value(operation, response, "name") == ""


@pytest.mark.parametrize(
    ("fuzzed", "excluded", "mutate_files", "expected"),
    [
        ({CREATE_BOOK, GET_BOOK}, set(), None, [(GET_BOOK, {"name": "books/1"})]),
        ({CREATE_BOOK, GET_BOOK}, set(), get_requires_version, [(GET_BOOK, {"name": "books/1", "version": 0})]),
        ({CREATE_BOOK}, set(), None, []),
        ({CREATE_BOOK, GET_BOOK}, {GET_BOOK}, None, []),
    ],
    ids=["linked", "target-requires-other-fields", "target-not-fuzzed", "target-excluded"],
)
def test_link_candidates(tmp_path, response_factory, fuzzed, excluded, mutate_files, expected):
    schema = load(tmp_path, mutate_files=mutate_files)
    operations = {label: schema.find_operation_by_label(label) for label in fuzzed}
    create = operations[CREATE_BOOK]
    candidates = schema.iter_link_candidates(
        operation=create,
        case=create.Case(body={"title": "Dune"}),
        response=response_factory.core(content=b"\n\x07books/1", headers={"grpc-status": ["0"]}),
        operations_by_label=operations,
        excluded_labels=excluded,
    )
    assert [
        (target.label, generate_many(target.as_strategy(**overrides), 1)[0].body) for target, overrides in candidates
    ] == expected


def server_errors(schema):
    with pytest.raises(FailureGroup) as exc:
        schema.as_state_machine().run()
    return [(type(failure), failure.title) for failure in exc.value.exceptions]


def serialize_bodies(schema):
    @schema.hook
    def map_body(context, body):
        message_class = message_factory.GetMessageClass(context.operation.definition.raw.input_type)
        return json_format.ParseDict(body, message_class()).SerializeToString()


@pytest.mark.parametrize(
    ("mutate_files", "hooks"),
    [(None, serialize_bodies), (create_also_pages, None)],
    ids=["serialized-bodies", "create-also-pages"],
)
def test_created_names_reach_get(ctx, tmp_path, mutate_files, hooks):
    schema = load(tmp_path, mutate_files=mutate_files, server=ctx.grpc.apps.library())
    if hooks is not None:
        hooks(schema)
    # Only `GetBook` on a created name fails.
    schema = schema.exclude(name="library.Library/ListBooks")
    assert server_errors(schema) == [(ServerError, "Server error")]


def test_linked_name_replaces_other_oneof_members(ctx, tmp_path):
    schema = load(tmp_path, mutate_files=name_in_oneof, server=ctx.grpc.apps.library())
    schema = schema.exclude(name="library.Library/ListBooks")
    # Calls on created names fail on purpose; anything else that fails is a request that did not encode.
    schema.config.checks.update(excluded_check_names=["not_a_server_error"])
    names = []

    class Machine(schema.as_state_machine()):
        def before_call(self, case):
            if case.operation.label == "library.Library/GetBook" and case.body.get("name", "").startswith("books/"):
                names.append(case.body)

    Machine.run()
    assert names
    assert all(set(body) == {"name"} for body in names)


def test_next_page_repeats_the_request(ctx, tmp_path):
    schema = load(tmp_path, mutate_files=list_filter, server=ctx.grpc.apps.library())
    schema.config.checks.update(excluded_check_names=["not_a_server_error"])
    issued = {}
    follow_ups = []

    class Machine(schema.as_state_machine()):
        def before_call(self, case):
            token = case.body.get("pageToken")
            if token in issued:
                follow_ups.append((issued[token], case.body))

        def after_call(self, response, case):
            if case.operation.label == "library.Library/ListBooks":
                token = response_value(case.operation, response, "next_page_token")
                if token:
                    issued[token] = case.body

    Machine.run()
    assert follow_ups
    assert all({**parent, "pageToken": child["pageToken"]} == child for parent, child in follow_ups)


def test_fields_the_server_reported_missing_are_set(ctx, tmp_path):
    schema = load(tmp_path, server=ctx.grpc.apps.library())
    schema.config.checks.update(excluded_check_names=["not_a_server_error"])
    store = ErrorFeedbackStore()
    store.record(
        Observation(
            operation_label=CREATE_BOOK,
            location=ParameterLocation.BODY,
            parameter_path=("title",),
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message="title is required",
        )
    )
    titles = []

    class Machine(schema.build_state_machine(feedback=FeedbackSources(error_feedback=store), link_calibration=None)):
        def before_call(self, case):
            if case.operation.label == CREATE_BOOK:
                titles.append(case.body.get("title", ""))

    Machine.run()
    assert titles
    assert [title for title in titles if not title] == []


def test_unsatisfiable_linked_method_is_reported(ctx, tmp_path):
    schema = load(tmp_path, mutate_files=get_requires_itself, server=ctx.grpc.apps.library())
    schema = schema.exclude(name="library.Library/ListBooks")
    with pytest.raises(UnsatisfiableSchema, match=f"Cannot generate test data for `{GET_BOOK}`"):
        schema.as_state_machine().run()


def test_rule_names_stay_distinct(tmp_path):
    machine = load(tmp_path, mutate_files=same_rule_names).as_state_machine()
    rules = [name for name, value in vars(machine).items() if hasattr(value, RULE_MARKER)]
    assert len(rules) == 8


def test_saved_scenarios_are_kept_per_schema(ctx, tmp_path):
    server = ctx.grpc.apps.library()
    keys = saved_example_keys(load(tmp_path, server=server).as_state_machine())
    assert keys == saved_example_keys(load(tmp_path, server=server).as_state_machine())
    more_links = load(tmp_path, mutate_files=create_also_pages, server=server)
    assert keys.isdisjoint(saved_example_keys(more_links.as_state_machine()))


def without_links(file):
    remove_method(file, "GetBook")
    remove_field(file, "ListBooksRequest", "page_token")


@pytest.mark.parametrize(
    ("mutate", "exclude", "expected"),
    [
        (None, True, "All methods that pass values to each other are excluded by filters"),
        (without_links, False, "No methods pass values to each other"),
    ],
    ids=["excluded", "absent"],
)
def test_no_links(tmp_path, mutate, exclude, expected):
    schema = load(tmp_path, mutate)
    if exclude:
        schema = schema.exclude(name="library.Library/GetBook").exclude(name="library.Library/ListBooks")
    with pytest.raises(NoLinksFound) as exc:
        schema.as_state_machine()
    assert str(exc.value) == expected
