# Quick Start Guide

**Estimated time: 5 minutes**

Schemathesis automatically finds bugs in your APIs by generating thousands of test cases from your OpenAPI or GraphQL schema. It catches edge cases that manual testing typically misses.

**Core benefits:**

- 🔍 **Discovers edge cases** that break your API with unexpected input
- ⚡ **Zero test maintenance** - adapts as your schema evolves  
- 🛡️ **Prevents regressions** by testing API contracts
- 📊 **Validates specification compliance** between implementation and documentation

## Try the demo

Test a sample API using [uv](https://docs.astral.sh/uv/){target=_blank}:

```bash
uvx schemathesis run https://example.schemathesis.io/openapi.json
```

Example output:

```
_____________________ POST /improper-input-type-handling _____________________

- Server error

[500] Internal Server Error:

    `{"success":false,"error":"invalid literal for int() with base 10: '\\n'"}`

Reproduce with:

    curl -X POST -H 'Content-Type: application/json' \
      -d '{"number": "\n\udbcd." }' 
      https://example.schemathesis.io/improper-input-type-handling
```

## Test your own API

=== "With authentication"
    ```bash
    uvx schemathesis run https://your-api.com/openapi.json \
      --header 'Authorization: Bearer your-token'
    ```

=== "Local development"
    ```bash
    uvx schemathesis run ./openapi.yaml --url http://localhost:8000
    ```

=== "pytest integration"
    ```python
    import schemathesis
    
    schema = schemathesis.openapi.from_url("https://your-api.com/openapi.json")
    
    @schema.parametrize()
    def test_api(case):
        # Automatically calls your API and validates the response
        case.call_and_validate()
    ```

## What's next?

!!! tip "Ready to dive deeper?"
    **[Complete Tutorial](tutorials/cli.md)** - 15-20 minute hands-on workflow with a realistic booking API

For release testing and security assessments, see [Optimizing for Maximum Bug Detection](guides/config-optimization.md)

**Reference guides:**

- **[CLI Reference](reference/cli.md)** - All available CLI options
- **[Configuration Reference](reference/configuration.md)** - Complete configuration reference
