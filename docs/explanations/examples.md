# Examples in API Schemas

Examples are sample values defined in your OpenAPI schema for request parameters and bodies. Some tools, such as [Dredd](https://dredd.org/en/latest/), test an API with those examples alone. Schemathesis uses them as one input source among several: the examples phase sends predictable, repeatable requests built from your examples, and the coverage and fuzzing phases generate a diverse range of inputs from the schema to reach edge cases your examples don't cover.

## Defining Examples in OpenAPI

In OpenAPI 3.0+, use `example` for a single example and `examples` for multiple values. You can define examples on properties and on the media type object, or reference external files using `externalValue`.

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
# Media-type-level example
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

Schemathesis automatically detects schema examples and uses them as test cases. Examples that fail validation against their own schema are skipped. For parameters and properties without examples, it uses their `default` when it matches the schema and generates a value from the schema otherwise. A required parameter's `default` counts as an example on its own.

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

Where `"John"` comes from the example, while the other values are generated from their schemas.

### Command-Line Interface

Run example-based tests only using the `--phases=examples` option:

```console
$ uvx schemathesis run --phases=examples https://example.schemathesis.io/openapi.json
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

!!! tip "Coming from Dredd?"

    If you rely on Dredd and find that a particular feature is missing in Schemathesis, please share your feedback via [GitHub Discussions](https://github.com/schemathesis/schemathesis/discussions).
