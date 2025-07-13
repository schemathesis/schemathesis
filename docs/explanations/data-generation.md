# Understanding Data Generation

This document explains how Schemathesis generates test data for your API, from raw schemas to complete HTTP requests. Understanding this process helps you write better extensions, troubleshoot unexpected behavior, and optimize your testing process.

## The Generation Hierarchy

Schemathesis data generation is built on the following hierarchy:

```
Hypothesis                    → Core data generation primitives
    ↓
hypothesis-jsonschema        → Schema-aware generation for OpenAPI
hypothesis-graphql           → Schema-aware generation for GraphQL  
    ↓
Schemathesis                 → Complete API testing workflow
```

**How it works:**

1. **Hypothesis** provides the foundation—strategies for generating strings, integers, objects, etc.
2. **hypothesis-jsonschema** and **hypothesis-graphql** translate your API schemas into Hypothesis strategies
3. **Schemathesis** orchestrates the entire process: parsing schemas, generating all request components, sending requests, and validating responses

This layered approach means Schemathesis inherits Hypothesis's features (like automatic shrinking) while adding API-specific behavior.

## Testing Phases

Schemathesis generates test cases through multiple phases, each targeting different aspects of API testing.

### Examples Phase

Uses `example` and `examples` from your schema, filling missing parts with generated data.

**Example:**
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

**Example:**
```yaml
# Schema: {"type": "string", "minLength": 2, "maxLength": 10}

# Produces: strings of length 1, 2, 3, 9, 10, 11
```

### Fuzzing Phase

Generates random data based on the schema constraints.

**Example:**
```yaml
# Schema: {"type": "integer", "minimum": 0, "maximum": 100}

# Produces: random integers like 0, 47, 100
# plus unusual values Hypothesis finds interesting
```

### Stateful Phase

Runs when OpenAPI schemas define links between operations. Creates sequences where response data feeds into subsequent requests.

**Example:**
```yaml
# Schema with links: POST /users → GET /users/{id}

# Produces: POST /users, extract ID, then GET /users/{extracted_id}
```

## Generation Modes

Schemathesis can generate two fundamentally different types of test data:

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

```bash
# Enable negative testing
schemathesis run --mode=negative https://api.example.com/openapi.json
```

## Serialization Process

The final step transforms generated objects into actual HTTP requests based on your API's media types.

**Media type support:**

Schemathesis supports many common media types out of the box, including JSON, XML (with OpenAPI XML annotations), form data, plain text, and others. For unsupported media types, you can add custom serializers.

**Example:**
```python
# Generated Python object
{"user_id": 123, "name": "test"}

# For application/json -> {"user_id": 123, "name": "test"}
# For application/xml -> <data><user_id>123</user_id><name>test</name></data>
```

If Schemathesis can't serialize data for a media type, those test cases are skipped. This keeps your test runs focused on actually testable scenarios.

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
