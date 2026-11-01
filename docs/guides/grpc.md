# Testing gRPC Services

Schemathesis tests unary gRPC methods: it generates request messages from the service descriptors, calls the server and checks the responses.

You need the `grpc` extra (`schemathesis[grpc]`). A server without reflection also needs [`buf`](https://buf.build/docs/installation) or `protoc` to build a descriptor set.

## Testing a server with reflection

If the server has [reflection](https://grpc.io/docs/guides/reflection/) enabled, point Schemathesis at it:

```bash
uvx --from 'schemathesis[grpc]' schemathesis run grpc://localhost:50051
```

```
 ✅  Loaded specification from grpc://localhost:50051 (in 0.05s)

     Base URL:         grpc://localhost:50051
     Specification:    gRPC
     Operations:       2 selected / 3 total
```

Schemathesis asks the server for its services and tests every unary method. It skips the reflection and `grpc.health.v1.Health` services. To test one method, pass `--include-name pkg.Users/GetUser`.

## Testing with a descriptor set

A server without reflection fails to load with "The server does not support reflection". Build a descriptor set from your `.proto` files in the directory that holds your `buf.yaml`, then pass the server address with `--url`:

```bash
buf build -o api.binpb
uvx --from 'schemathesis[grpc]' schemathesis run api.binpb --url grpc://localhost:50051
```

With `protoc`, include the imported files in the set:

```bash
protoc --include_imports --descriptor_set_out=api.binpb users.proto
```

Files ending in `.binpb`, `.pb` or `.protoset` load as descriptor sets. For a TLS server, pass a `grpcs://` URL.

## TLS

Use `grpcs://` and pass the CA bundle that signed the server certificate. For a self-signed certificate, pass the certificate itself:

```bash
uvx --from 'schemathesis[grpc]' schemathesis run grpcs://api.example.com:443 \
    --tls-verify ca.pem
```

For mutual TLS, add `--request-cert client.pem --request-cert-key client.key`. gRPC targets do not accept `--tls-verify=false`. Without a port in the URL, Schemathesis connects to port 443.

## Metadata and authentication

Schemathesis sends headers as call metadata, with lowercased keys, on every call including reflection. Keys ending in `-bin` take base64 values:

```bash
uvx --from 'schemathesis[grpc]' schemathesis run grpc://localhost:50051 \
    -H 'Authorization: Bearer token' \
    -H 'x-trace-bin: AAE='
```

The same headers in `schemathesis.toml`:

```toml
headers = { Authorization = "Bearer ${API_TOKEN}" }
```

## Reading failures

A failure shows the gRPC status and its message where an HTTP failure shows the status line, and a [`grpcurl`](https://github.com/fullstorydev/grpcurl) command to reproduce the call:

```
- ST101 Server error

INTERNAL: negative id:

    <EMPTY>

Reproduce with:

    grpcurl -plaintext -H 'x-schemathesis-testcaseid: LGSYov' -d '{"userId": -1}' localhost:50051 pkg.Users/GetUser

    st replay LGSYov
```

`<EMPTY>` marks a response without a body. With a descriptor set, the command passes it to `grpcurl` through `-protoset`. `st replay` reruns the case with Schemathesis; `st` is the short name of the `schemathesis` command.

Statuses whose HTTP equivalent is a 5xx count as server errors: `UNKNOWN`, `INTERNAL`, `DATA_LOSS` and `UNAVAILABLE`. `UNIMPLEMENTED` means the server does not serve the method, so Schemathesis does not report it as a failure; a method that returns nothing else gets a [`not_served`](../reference/warnings.md#not_served) warning. `response_schema_conformance` fails a response that does not decode as the method's output message. For a reflection target it also fails a response with fields the server's own descriptors do not declare.

## Python and pytest

`schemathesis.grpc.from_url` and `schemathesis.grpc.from_path` load the same schemas for use with `@schema.parametrize()`:

```python
import schemathesis

schema = schemathesis.grpc.from_url("grpc://localhost:50051")


@schema.parametrize()
def test_api(case):
    case.call_and_validate()
```

## Current limitations

- Schemathesis counts streaming methods but does not test them.
- Only the coverage, fuzzing and [stateful](../explanations/stateful.md) [phases](../explanations/data-generation.md#testing-phases) run: no examples phase, and no [negative test cases](../explanations/data-generation.md#generation-modes).
- Schemathesis leaves `google.protobuf.Any` fields out of requests and reports them as [`unsupported_field`](../reference/warnings.md#unsupported_field) warnings. Fill them with a [`map_body`](extending.md) hook, or a `before_call` hook for the coverage phase, which skips `map_body`.
- Schemathesis does not read `.proto` files directly; build a descriptor set first.
- HAR, VCR and pytest reports and proxies are not available for gRPC targets.
- Official Docker images and the GitHub Action do not include the `grpc` extra. Run gRPC targets from a Python installation with `pip install 'schemathesis[grpc]'`, or use `uvx --from 'schemathesis[grpc]' schemathesis ...`.
