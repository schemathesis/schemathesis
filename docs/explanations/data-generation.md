# Understanding Data Generation

This guide explains how Schemathesis generates test data for your API, from raw schemas to complete HTTP requests. Understanding this process helps you write better extensions, troubleshoot unexpected behavior, and optimize your testing strategy.

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

This layered approach means Schemathesis inherits Hypothesis's powerful features (like automatic shrinking) while adding API-specific intelligence.

## Testing Phases

!!! info "How phases work together"
    These phases run independently — each generates its own test cases based on whether conditions are met. You can control which phases run with `--phases=examples,coverage,fuzzing,stateful`.

Schemathesis generates test cases through multiple independent phases, each targeting different aspects of API testing.

### Examples Phase

**When it runs:** When your schema contains `example` or `examples` fields

**What it produces:** Test cases using those examples, with generated data filling any missing parameters

**Why it matters:** Tests real-world data patterns from your documentation instead of purely random values

**Example:**
```yaml
# Schema
parameters:
  - name: limit
    schema:
      type: integer
      examples: [10, 50, 100]

# Produces: 3 test cases with limit=10, limit=50, limit=100
```

### Coverage Phase

**When it runs:** Always (for schemas with defined constraints)

**What it produces:** Boundary values, all enum/const values, property combinations, and constraint violations

**Why it matters:** Catches edge cases that random testing might miss through systematic exploration

**Example:**
```yaml
# Schema: {"type": "string", "minLength": 2, "maxLength": 10}

# Produces: strings of length 2, 3, 9, 10 (boundaries)
```

### Fuzzing Phase

**When it runs:** Always

**What it produces:** Random, diverse data within your schema constraints

**Why it matters:** Discovers unexpected edge cases through Hypothesis's property-based testing approach

**Example:**
```yaml
# Schema: {"type": "integer", "minimum": 0, "maximum": 100}

# Produces: random integers like 0, 47, 100
# plus unusual values Hypothesis finds interesting
```

### Stateful Phase

**When it runs:** When your OpenAPI schema defines links between operations

**What it produces:** Sequences of API calls where response data feeds into subsequent requests

**Why it matters:** Finds issues that only appear when operations are combined in specific orders

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

**How it works:** Schemathesis mutates your schema to produce invalid data.

```bash
# Enable negative testing
schemathesis run --mode=negative https://api.example.com/openapi.json
```

## Serialization Process

The final step transforms generated Python objects into actual HTTP requests based on your API's media types.

**How it works:**

1. Schemathesis generates Python data structures (dicts, lists, strings, etc.)
2. Based on the operation's `Content-Type`, it serializes the data appropriately
3. The serialized data becomes the HTTP request body

**Media type support:**

Schemathesis supports many common media types out of the box, including JSON, XML (with OpenAPI XML annotations), form data, plain text, and others. For unsupported media types, you can add custom serializers.

**Example:**
```python
# Generated Python object
{"user_id": 123, "name": "test"}

# For application/json → {"user_id": 123, "name": "test"}
# For application/xml → <data><user_id>123</user_id><name>test</name></data>
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
{"name": "a", "age": 42}  # Only essential data
```

Shrinking is enabled by default. Disable with `--no-shrink` for faster runs.

## How Many Test Cases Does Schemathesis Generate?

**Short answer:** Up to `--max-examples` per operation (default: 100), but often fewer.

**Why fewer:**

- **Limited possibilities:** Schema with `enum: ["A", "B"]` only generates 2 test cases
- **Phase limits:** Examples phase generates exactly the number of examples in your schema
- **Coverage phase:** Generates a deterministic count based on your constraints

**Why more:** 

- **Rejected cases:** Invalid data that can't be serialized gets discarded and retried
- **Shrinking:** Additional test cases generated when minimizing failures
