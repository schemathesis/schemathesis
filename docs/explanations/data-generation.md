# Understanding Data Generation

Point Schemathesis at one API operation and it may send hundreds of requests, including deliberately invalid ones for OpenAPI and GraphQL. Several phases contribute test cases, and a failure triggers extra requests to shrink it to a minimal reproduction. gRPC generation is positive-only today.

## The Generation Hierarchy

Schemathesis is structured as four phases (Examples, Coverage, Fuzzing, Stateful), plus feedback loops that learn from server responses and influence subsequent test cases. Every phase uses Hypothesis with schema-based generators: a built-in JSON Schema strategy engine for OpenAPI and converted Protobuf descriptors, and `hypothesis-graphql` for GraphQL. What differs between phases is how each one chooses inputs and which validity mode it targets. Not every specification supports every phase or mode; gRPC skips Examples and negative generation.

What each layer contributes:

1. **Hypothesis** — primitive strategies (strings, integers, objects), shrinking, and the example database.
2. **Schema strategy engines** — translate JSON Schema, GraphQL fragments, or converted Protobuf descriptors into Hypothesis strategies. Used by every supported phase as the schema-driven value source; the calling phase selects the validity mode when the specification supports it.
3. **Schemathesis** — the phase pipeline, HTTP and gRPC transports, response checks, and feedback loops that learn from what the server returns. See [Adaptive Testing](adaptive-testing.md).

Schemathesis inherits Hypothesis's shrinking and example database; the feedback loop is what lets it learn server-side validation (OpenAPI) and reuse real values across operations. When a Python app is loaded via `from_asgi`/`from_wsgi`, it also reuses literals read from the application's own source as candidate inputs - see [Testing Python Apps](../guides/python-apps.md#reusing-values-from-your-source).

## Testing Phases

Each phase adds its own test cases for an operation, so the number of requests an operation receives is the sum across phases.

### Examples Phase

Uses `example` and `examples` from your schema, filling missing parts with `default` values or generated data.

```yaml
# Schema
parameters:
  - name: limit
    in: query
    schema:
      type: integer
      examples: [10, 50, 100]

# Produces: 3 test cases with limit=10, limit=50, limit=100
```

See [Examples in API Schemas](examples.md) for how examples are combined.

### Coverage Phase

Aims to exhaustively cover boundary values for every constraint defined in the schema.

```yaml
# Schema: {"type": "string", "minLength": 2, "maxLength": 10}

# Produces: strings of length 1, 2, 3, 9, 10, 11
```

Request body strings without `maxLength` also get one 65,537-character value. It exceeds limits servers often enforce without declaring them, such as database column sizes or 16-bit length prefixes.

For GraphQL, each case changes one argument or input field of a fixed valid query, or adds one field to its selection, up to three levels deep:

```graphql
# Schema: enum Genre { DRAMA COMEDY }  type Query { books(limit: Int!, genre: Genre): [Book] }

# Valid:   books(limit: 0), books(limit: -2147483648), books(limit: 0, genre: null), books(limit: 0, genre: DRAMA)
# Invalid: books, books(limit: "a"), books(limit: 2147483648), books(limit: 0, genre: invalid)
```

For gRPC, the coverage phase walks Protobuf field types, validation rules, enum values, oneofs, and message boundaries to build valid request messages for unary methods.

### Fuzzing Phase

Generates random data based on the schema constraints.

```yaml
# Schema: {"type": "integer", "minimum": 0, "maximum": 100}

# Produces: random integers like 0, 47, 100
# plus unusual values Hypothesis finds interesting
```

### Stateful Phase

Runs when Schemathesis knows how operations connect: OpenAPI links and inferred dependencies, GraphQL type-graph connections, or gRPC method and field names that follow Google API conventions. Creates sequences where response data feeds into subsequent requests. See [Stateful Testing](stateful.md).

```yaml
# Connection: POST /users -> GET /users/{id}

# Produces: POST /users, extract ID, then GET /users/{extracted_id}
```

## Generation Modes

For OpenAPI and GraphQL, the Coverage, Fuzzing, and Stateful phases generate both valid and invalid data by default, which is why some requests are deliberately wrong. The Examples phase is positive-only. gRPC supports positive generation in the Coverage, Fuzzing, and Stateful phases; selecting negative-only mode skips its operations.

| Mode | Generates |
|------|-----------|
| `all` *(default)* | Valid and invalid data |
| `positive` | Only valid data |
| `negative` | Only invalid data |

```bash
uvx schemathesis run https://api.example.com/openapi.json
uvx schemathesis run --mode=negative https://api.example.com/openapi.json
```

### Positive Testing

Generates data that **should be accepted** by your API — valid according to your schema.

```python
# Schema: {"type": "string", "minLength": 3}
# Positive examples: "abc", "hello", "test123"
```

### Negative Testing

Generates data that **should be rejected** by your API because it is deliberately invalid according to your schema. Schemathesis mutates your schema to produce it.

```python
# Schema: {"type": "string", "minLength": 3}
# Negative examples: 42, [], "", "ab"
```

### GraphQL Negative Testing

Negative testing works for GraphQL by generating queries with:

- **Wrong types** — Passing a String where an Int is expected
- **Invalid enum values** — Using values not defined in the enum
- **Missing required arguments** — Omitting non-nullable arguments

!!! note "Skipped operations"
    Operations without required arguments are skipped in `--mode=negative` (nothing to invalidate). With `--mode=all`, they fall back to positive testing.

## Serialization Process

The final step transforms generated objects into requests for the API's transport. HTTP APIs serialize values according to their media types; gRPC APIs encode generated Protobuf messages and send them with `grpcio`.

Schemathesis supports many common media types out of the box, including JSON, XML (with OpenAPI XML annotations), form data, plain text, and others. For unsupported media types, you can add custom serializers.

```python
# Generated Python object
{"user_id": 123, "name": "test"}

# For application/json -> {"user_id": 123, "name": "test"}
# For application/xml -> <data><user_id>123</user_id><name>test</name></data>
```

If Schemathesis can't serialize data for a media type, those test cases are skipped.

## Shrinking and Failure Handling

When Schemathesis finds a failing test case, it automatically **shrinks** it to the minimal example that reproduces the failure. Each shrinking attempt is another request.

```python
# Before shrinking
{"name": "Very long user name", "age": 42, "metadata": {...}}

# After shrinking: only data that triggers the failure
{"name": "a", "age": 42}
```

!!! important
    Shrinking is enabled by default. Disable with `--no-shrink` for faster test runs.

## How Many Test Cases Does Schemathesis Generate?

`--max-examples` caps the cases generated by the fuzzing phase per operation; the examples and coverage phases add their own cases. The default is 100. In the stateful phase, the same setting limits the number of generated call sequences.

In positive mode, the fuzzing phase often stays below the cap when the schema allows few valid inputs: `enum: ["A", "B"]` has only 2 valid values. Negative mode has no such limit, since almost any other value violates the schema.

The other phases do not depend on the cap:

- **Examples phase:** one case per combination Schemathesis builds from the examples in your schema (see [Examples in API Schemas](examples.md#multiple-examples-strategy)). Examples that fail validation against their schema are dropped, and when real values captured from earlier responses are available, extra combinations that use them are added.
- **Coverage phase:** a deterministic count based on your constraints.

Generation does more work than the case count shows:

- **Rejected cases:** invalid data that can't be serialized gets discarded and retried.
- **Shrinking:** additional requests are sent while minimizing a failure.
