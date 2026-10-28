from google.protobuf import descriptor_pb2, timestamp_pb2

FieldDescriptorProto = descriptor_pb2.FieldDescriptorProto


def users_descriptor_set() -> bytes:
    timestamp = descriptor_pb2.FileDescriptorProto()
    timestamp_pb2.DESCRIPTOR.CopyToProto(timestamp)
    users = descriptor_pb2.FileDescriptorProto(
        name="users.proto",
        package="pkg",
        syntax="proto3",
        dependency=["google/protobuf/timestamp.proto", "google/longrunning/operations.proto"],
    )
    request = users.message_type.add(name="GetUserRequest")
    request.field.add(
        name="user_id",
        json_name="userId",
        number=1,
        type=FieldDescriptorProto.TYPE_INT32,
        label=FieldDescriptorProto.LABEL_OPTIONAL,
    )
    user = users.message_type.add(name="User")
    user.field.add(
        name="name",
        json_name="name",
        number=1,
        type=FieldDescriptorProto.TYPE_STRING,
        label=FieldDescriptorProto.LABEL_OPTIONAL,
    )
    user.field.add(
        name="created_at",
        json_name="createdAt",
        number=2,
        type=FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".google.protobuf.Timestamp",
        label=FieldDescriptorProto.LABEL_OPTIONAL,
    )
    service = users.service.add(name="Users")
    service.method.add(
        name="GetUser",
        input_type=".pkg.GetUserRequest",
        output_type=".pkg.User",
        options=descriptor_pb2.MethodOptions(deprecated=True),
    )
    service.method.add(
        name="WatchUsers", input_type=".pkg.GetUserRequest", output_type=".pkg.User", server_streaming=True
    )
    users.service.add(name="Admin").method.add(
        name="Reset", input_type=".pkg.GetUserRequest", output_type=".google.longrunning.Operation"
    )
    # Imported for its message type; its service belongs to another API.
    operations = descriptor_pb2.FileDescriptorProto(
        name="google/longrunning/operations.proto", package="google.longrunning", syntax="proto3"
    )
    operations.message_type.add(name="Operation")
    operations.message_type.add(name="GetOperationRequest")
    operations.service.add(name="Operations").method.add(
        name="GetOperation",
        input_type=".google.longrunning.GetOperationRequest",
        output_type=".google.longrunning.Operation",
    )
    return descriptor_pb2.FileDescriptorSet(file=[timestamp, users, operations]).SerializeToString()
