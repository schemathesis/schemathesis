# Examples in API Schemas

Examples are sample values defined in your OpenAPI schema for request parameters and bodies.

Schemathesis supports:

- **Example-based testing**: Uses fixed input values from your schema to produce predictable, repeatable tests.
- **Property-based testing**: Generates a diverse range of inputs dynamically to expose unexpected edge cases.

## Defining Examples in OpenAPI

In OpenAPI 3.0+, use `example` for a single example and `examples` for multiple values. You can define examples at both the property and operation levels, or reference external files using `externalValue`.

```yaml
# Single example using the 'example' keyword
schema:
  type: object
  properties:
    name:
      type: string
      example: "John Doe"
```

```yaml
# Property-level example
properties:
  name:
    type: string
    example: "John Doe"
  age:
    type: integer
    example: 30
```

```yaml
# Operation-level example
requestBody:
  content:
    application/json:
      schema:
        $ref: '#/components/schemas/User'
      example:
        name: "John Doe"
        age: 30
```

```yaml
content:
  application/json:
    schema:
      $ref: '#/components/schemas/User'
    examples:
      user:
        summary: "A typical user"
        # Schemathesis will load and cache external examples during testing.
        externalValue: 'http://example.com/examples/user.json'
```

!!! tip "OpenAPI 2.0 Support"

    In OpenAPI 2.0, use the `example` keyword or `x-examples` extension for multiple examples.

    ```yaml
    # OpenAPI 2.0 with multiple examples
    definitions:
      User:
        type: object
        properties:
          name:
            type: string
        x-examples:
          - name: "John Doe"
          - name: "Jane Smith"
    ```

## Using Examples in Tests

Schemathesis automatically detects schema examples and uses them as test cases. For parameters without examples, it generates minimal valid values to ensure every operation is tested.

```yaml
# Schema
schema:
  type: object
  properties:
    name:
      type: string
      example: "John"
    age:
      type: integer
    address:
      type: string
```

This would generate test cases like:

```json
{"name": "John", "age": 42, "address": "abc"}
```

Where `"John"` comes from the example, while the other values are minimal values that satisfy the schema constraints.

### Command-Line Interface

Run example-based tests only using the `--phases=examples` option:

```console
$ st run --phases=examples https://example.schemathesis.io/openapi.json
```

This restricts testing to the examples phase, skipping other testing phases like coverage, fuzzing, and stateful testing.

### Multiple Examples Strategy

Schemathesis uses a round-robin strategy to evenly distribute test cases across multiple examples:

```yaml
# Schema
properties:
  name:
    type: string
    example: "John"
  age:
    type: integer
    examples: [25, 30, 35]
```

Schemathesis will generate test cases using each age value:
```json
{"name": "John", "age": 25, ...}
{"name": "John", "age": 30, ...}
{"name": "John", "age": 35, ...}
```

## Differences with Dredd

Unlike [Dredd](https://dredd.org/en/latest/), which only uses schema examples, Schemathesis uses both predefined examples and generated data. Schemathesis also includes test case reduction and stateful testing.

!!! tip "Feedback"

    If you rely on Dredd and find that a particular feature is missing in Schemathesis, please share your feedback via [GitHub Discussions](https://github.com/schemathesis/schemathesis/discussions).
