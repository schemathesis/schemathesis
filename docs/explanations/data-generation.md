# Understanding Data Generation

This document explains how Schemathesis generates test data for your API, from raw schemas to complete HTTP requests.

## The Generation Hierarchy

Schemathesis is structured as four phases (Examples, Coverage, Fuzzing, Stateful), plus a feedback loop on OpenAPI that learns from server responses and influences subsequent test cases. Every phase uses Hypothesis with schema-based generators (`hypothesis-jsonschema` for OpenAPI, `hypothesis-graphql` for GraphQL) to drive values from the schema; what differs between phases is how each one chooses inputs and which validity mode it targets.

What each layer contributes:

1. **Hypothesis** — primitive strategies (strings, integers, objects), shrinking, and the example database.
2. **hypothesis-jsonschema / hypothesis-graphql** — translate JSON Schema / GraphQL fragments into Hypothesis strategies. Used by every phase as the schema-driven value source; the validity mode (positive, negative, mixed) is set by the calling phase.
3. **Schemathesis** — the four-phase pipeline, HTTP transport, response checks, and a feedback loop that learns from what the server returns. See [Adaptive Testing](adaptive-testing.md).

Schemathesis inherits Hypothesis's shrinking and example database; the feedback loop is what lets it learn server-side validation (OpenAPI) and reuse real values across operations.

## Testing Phases

### Examples Phase

Uses `example` and `examples` from your schema, filling missing parts with generated data.

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

### Coverage Phase

Aims to exhaustively cover boundary values for every constraint defined in the schema.

```yaml
# Schema: {"type": "string", "minLength": 2, "maxLength": 10}

# Produces: strings of length 1, 2, 3, 9, 10, 11
```

### Fuzzing Phase

Generates random data based on the schema constraints.

```yaml
# Schema: {"type": "integer", "minimum": 0, "maximum": 100}

# Produces: random integers like 0, 47, 100
# plus unusual values Hypothesis finds interesting
```

### Stateful Phase

Runs when OpenAPI schemas define links between operations. Creates sequences where response data feeds into subsequent requests.

```yaml
# Schema with links: POST /users → GET /users/{id}

# Produces: POST /users, extract ID, then GET /users/{extracted_id}
```

## Generation Modes

**By default, both positive and negative testing are enabled** — you don't need any extra flags.

| Mode | Generates |
|------|-----------|
| `all` *(default)* | Valid and invalid data |
| `positive` | Only valid data |
| `negative` | Only invalid data |

```bash
schemathesis run https://api.example.com/openapi.json
schemathesis run --mode=negative https://api.example.com/openapi.json
```

### Positive Testing

Generates data that **should be accepted** by your API — valid according to your schema.

```python
# Schema: {"type": "string", "minLength": 3}
# Positive examples: "abc", "hello", "test123"
```

### Negative Testing

Generates data that **should be rejected** by your API — deliberately invalid according to your schema.

```python
# Schema: {"type": "string", "minLength": 3}
# Negative examples: 42, [], "", "ab"
```

!!! tip "How it works"
    Schemathesis mutates your schema to produce invalid data.

### GraphQL Negative Testing

Negative testing works for GraphQL by generating queries with:

- **Wrong types** — Passing a String where an Int is expected
- **Invalid enum values** — Using values not defined in the enum
- **Missing required arguments** — Omitting non-nullable arguments

!!! note "Skipped operations"
    Operations without required arguments are skipped in `--mode=negative` (nothing to invalidate). With `--mode=all`, they fall back to positive testing.

## Serialization Process

The final step transforms generated objects into actual HTTP requests based on your API's media types.

Schemathesis supports many common media types out of the box, including JSON, XML (with OpenAPI XML annotations), form data, plain text, and others. For unsupported media types, you can add custom serializers.

```python
# Generated Python object
{"user_id": 123, "name": "test"}

# For application/json -> {"user_id": 123, "name": "test"}
# For application/xml -> <data><user_id>123</user_id><name>test</name></data>
```

If Schemathesis can't serialize data for a media type, those test cases are skipped.

## Shrinking and Failure Handling

When Schemathesis finds a failing test case, it automatically **shrinks** it to the minimal example that reproduces the failure.

**Before shrinking**

```python
{"name": "Very long user name", "age": 42, "metadata": {...}}
```

**After shrinking**

```python
{"name": "a", "age": 42}  # Only data that triggers a failure
```


!!! important
    Shrinking is enabled by default. Disable with `--no-shrink` for faster test runs.

## How Many Test Cases Does Schemathesis Generate?

**Short answer:** Up to `--max-examples` per operation (default: 100), but often fewer.

**Why fewer:**

- **Limited possibilities:** Schema with `enum: ["A", "B"]` only generates 2 test cases
- **Phase limits:** Examples phase generates exactly the number of examples in your schema
- **Coverage phase:** Generates a deterministic count based on your constraints

**Why more:** 

- **Rejected cases:** Invalid data that can't be serialized gets discarded and retried
- **Shrinking:** Additional test cases generated when minimizing failures
