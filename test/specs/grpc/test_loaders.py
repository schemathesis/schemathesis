import gc
import os
import subprocess
import sys
import weakref

import pytest
from google.protobuf import descriptor_pb2

import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.core.errors import LoaderError, LoaderErrorKind, OperationNotFound, RejectedSchemaDefinition
from schemathesis.core.statistic import FilterCriterion, UnmatchedFilter
from schemathesis.engine.run.unit._errors import prefer_spec_error
from test.apps.catalog.grpc.descriptors import users_descriptor_set


@pytest.fixture
def users_path(tmp_path):
    path = tmp_path / "api.binpb"
    path.write_bytes(users_descriptor_set())
    return path


@pytest.fixture
def schema(users_path):
    return schemathesis.grpc.from_path(users_path)


def test_from_path_lists_unary_operations(schema):
    assert [result.ok().label for result in schema.get_all_operations()] == ["pkg.Users/GetUser", "pkg.Admin/Reset"]


def test_operation_fields(schema):
    operation = schema["pkg.Users"]["GetUser"]
    assert (operation.label, operation.path, operation.method, operation.tags) == (
        "pkg.Users/GetUser",
        "/pkg.Users/GetUser",
        "post",
        ["pkg.Users"],
    )


def test_services_expose_unary_methods(schema):
    assert {service: (len(schema[service]), list(schema[service])) for service in schema} == {
        "pkg.Users": (1, ["GetUser"]),
        "pkg.Admin": (1, ["Reset"]),
    }


def test_operations_use_configured_base_url(users_path):
    config = SchemathesisConfig.from_dict({"base-url": "grpc://127.0.0.1:50051"})
    schema = schemathesis.grpc.from_path(users_path, config=config)
    assert schema["pkg.Admin"]["Reset"].base_url == "grpc://127.0.0.1:50051"


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("pkg.Admin/Reset", "pkg.Admin/Reset"),
        ("pkg.Admin/Missing", None),
        ("pkg.Missing/Reset", None),
        ("pkg.Users/WatchUsers", None),
    ],
    ids=["found", "unknown-method", "unknown-service", "streaming"],
)
def test_find_operation_by_label(schema, label, expected):
    operation = schema.find_operation_by_label(label)
    assert (operation.label if operation is not None else None) == expected


@pytest.mark.parametrize(
    ("service", "message"),
    [
        ("pkg.User", "`pkg.User` service not found. Did you mean `pkg.Users`?"),
        ("other.Service", "`other.Service` service not found"),
    ],
    ids=["close-match", "no-match"],
)
def test_missing_service(schema, service, message):
    with pytest.raises(OperationNotFound) as exc_info:
        schema[service]
    assert exc_info.value.message == message


@pytest.mark.parametrize(
    ("method", "message"),
    [
        ("GetUsr", "`GetUsr` method not found. Did you mean `GetUser`?"),
        ("Purge", "`Purge` method not found"),
        ("WatchUsers", "`WatchUsers` is a streaming method; only unary methods are supported"),
    ],
    ids=["close-match", "no-match", "streaming"],
)
def test_missing_method(schema, method, message):
    with pytest.raises(KeyError) as exc_info:
        schema["pkg.Users"][method]
    assert exc_info.value.args[0] == message


@pytest.mark.parametrize(
    ("narrow", "expected"),
    [
        (lambda schema: schema.include(name="pkg.Admin/Reset"), ["pkg.Admin/Reset"]),
        (lambda schema: schema.include(path="/pkg.Users/GetUser"), ["pkg.Users/GetUser"]),
        (lambda schema: schema.include(method="POST"), ["pkg.Users/GetUser", "pkg.Admin/Reset"]),
        (lambda schema: schema.include(tag="pkg.Users"), ["pkg.Users/GetUser"]),
        (lambda schema: schema.include(operation_id="GetUser"), []),
        (lambda schema: schema.exclude(deprecated=True), ["pkg.Admin/Reset"]),
    ],
    ids=["name", "path", "method", "tag", "operation-id", "deprecated"],
)
def test_filters(schema, narrow, expected):
    assert [result.ok().label for result in narrow(schema).get_all_operations()] == expected


def test_statistic_counts_streaming_as_unselected(schema):
    statistic = schema.statistic
    assert (statistic.operations.total, statistic.operations.selected, statistic.unmatched_filters) == (3, 2, [])


def test_statistic_reports_unmatched_filters(schema):
    statistic = schema.include(name="pkg.Admin/Rest").statistic
    assert (statistic.operations.total, statistic.operations.selected, statistic.unmatched_filters) == (
        3,
        0,
        [
            UnmatchedFilter(
                include=True,
                criteria=[FilterCriterion(attribute="label", value="pkg.Admin/Rest", is_regex=False)],
                suggestion="pkg.Admin/Reset",
                source="selection",
            )
        ],
    )


def test_rejected_definition_is_reported_as_is(schema):
    exc = RejectedSchemaDefinition("Invalid definition")
    assert prefer_spec_error(exc, schema["pkg.Admin"]["Reset"]) is exc


def test_invalid_descriptor_set(tmp_path):
    path = tmp_path / "api.binpb"
    path.write_bytes(b"not a descriptor")
    with pytest.raises(LoaderError) as exc_info:
        schemathesis.grpc.from_path(path)
    assert (exc_info.value.kind, exc_info.value.message) == (
        LoaderErrorKind.GRPC_INVALID_DESCRIPTOR_SET,
        "The file is not a serialized `FileDescriptorSet`. Build one with `buf build -o api.binpb` or "
        "`protoc --include_imports --descriptor_set_out=api.binpb`",
    )


def test_descriptor_set_without_imports(tmp_path):
    file = descriptor_pb2.FileDescriptorProto(
        name="users.proto", package="pkg", syntax="proto3", dependency=["google/protobuf/timestamp.proto"]
    )
    path = tmp_path / "api.binpb"
    path.write_bytes(descriptor_pb2.FileDescriptorSet(file=[file]).SerializeToString())
    with pytest.raises(LoaderError) as exc_info:
        schemathesis.grpc.from_path(path)
    assert (exc_info.value.kind, exc_info.value.message) == (
        LoaderErrorKind.GRPC_INVALID_DESCRIPTOR_SET,
        "The descriptor set is incomplete: `users.proto` imports `google/protobuf/timestamp.proto`, which it does "
        "not contain. Build one with `buf build -o api.binpb` or "
        "`protoc --include_imports --descriptor_set_out=api.binpb`",
    )


def test_filter_naming_a_streaming_method_is_not_unmatched(schema):
    statistic = schema.include(name="pkg.Users/WatchUsers").statistic
    assert (statistic.operations.selected, statistic.unmatched_filters) == (0, [])


def write_descriptor_set(tmp_path, files):
    path = tmp_path / "api.binpb"
    path.write_bytes(descriptor_pb2.FileDescriptorSet(file=files).SerializeToString())
    return path


def users_files():
    return list(descriptor_pb2.FileDescriptorSet.FromString(users_descriptor_set()).file)


def test_files_load_in_any_order(tmp_path):
    schema = schemathesis.grpc.from_path(write_descriptor_set(tmp_path, users_files()[::-1]))
    assert [result.ok().label for result in schema.get_all_operations()] == ["pkg.Users/GetUser", "pkg.Admin/Reset"]


def unresolvable_type_name(files):
    files[1].service[1].method[0].input_type = ".pkg.Missing"
    return "The descriptor set does not load: couldn't resolve name '.pkg.Missing'"


def import_cycle(files):
    files[0].dependency.append("users.proto")
    return "The descriptor set does not load: `google/protobuf/timestamp.proto` imports itself through its imports"


# The pure-Python runtime builds descriptors lazily and reports problems differently.
@pytest.mark.parametrize("implementation", ["upb", "python"])
@pytest.mark.parametrize("corrupt", [unresolvable_type_name, import_cycle])
def test_broken_descriptor_set(tmp_path, implementation, corrupt):
    files = users_files()
    expected = corrupt(files)
    result = subprocess.run(
        [sys.executable, "-c", LOAD_AND_REPORT, str(write_descriptor_set(tmp_path, files))],
        env={**os.environ, "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION": implementation},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == f"GRPC_INVALID_DESCRIPTOR_SET\n{expected}\n"


# Without a native build for the interpreter, protobuf runs pure Python whatever the variable asks for.
LOAD_AND_REPORT = """
import sys
import schemathesis
from schemathesis.core.errors import LoaderError
try:
    schemathesis.grpc.from_path(sys.argv[1])
except LoaderError as exc:
    print(exc.kind.name)
    print(exc.message)
"""


def test_missing_grpc_extra(users_path):
    result = subprocess.run(
        [sys.executable, "-c", BLOCK_PROTOBUF_AND_LOAD, str(users_path)], capture_output=True, text=True
    )
    assert result.stderr.splitlines()[-1] == (
        "ImportError: Loading gRPC schemas requires the `grpc` extra: `pip install schemathesis[grpc]`"
    )


BLOCK_PROTOBUF_AND_LOAD = """
import sys
sys.modules["google.protobuf"] = None
import schemathesis
schemathesis.grpc.from_path(sys.argv[1])
"""


def test_before_load_schema_hook_sees_descriptor_files(users_path):
    seen = []

    @schemathesis.hook
    def before_load_schema(context, raw_schema):
        seen.append([file["name"] for file in raw_schema["file"]])

    schemathesis.grpc.from_path(users_path)
    assert seen == [["google/protobuf/timestamp.proto", "users.proto", "google/longrunning/operations.proto"]]


def test_empty_descriptor_set(tmp_path):
    with pytest.raises(LoaderError) as exc_info:
        schemathesis.grpc.from_path(write_descriptor_set(tmp_path, []))
    assert (exc_info.value.kind, exc_info.value.message) == (
        LoaderErrorKind.GRPC_INVALID_DESCRIPTOR_SET,
        "The descriptor set contains no files. Build one with `buf build -o api.binpb` or "
        "`protoc --include_imports --descriptor_set_out=api.binpb`",
    )


@pytest.mark.parametrize("level", ["service", "file"])
def test_deprecation_inherited_from_service_and_file(tmp_path, level):
    files = users_files()
    users = files[1]
    if level == "service":
        users.service[1].options.deprecated = True
    else:
        users.options.deprecated = True
    schema = schemathesis.grpc.from_path(write_descriptor_set(tmp_path, files))
    assert [result.ok().label for result in schema.exclude(deprecated=True).get_all_operations()] == []


def test_filtering_does_not_keep_operations_alive(schema):
    operation = next(schema.get_all_operations()).ok()
    reference = weakref.ref(operation)
    del operation
    gc.collect()
    assert reference() is None


def test_repr_omits_descriptors(schema):
    assert repr(schema) == "<GrpcSchema>"
