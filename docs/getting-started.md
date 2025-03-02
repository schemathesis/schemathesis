# Getting Started with Schemathesis

Schemathesis automatically generates and runs API tests from your OpenAPI or GraphQL schema to find bugs and spec violations.

## What is Schemathesis?

Schemathesis is a property-based testing tool that:

- Uses your API schema to generate test cases automatically
- Validates responses against schema definitions
- Finds edge cases and bugs without manual test writing
- Works with OpenAPI (Swagger) and GraphQL schemas

## Installation

### Using uv (recommended)

[uv](https://docs.astral.sh/uv/) is a fast Python package installer and environment manager:

```console
$ uv pip install schemathesis
```

### Run without installing

[uvx](https://docs.astral.sh/uvx/) (part of the uv ecosystem) lets you run Schemathesis directly without installation:

```console
$ uvx schemathesis run https://example.schemathesis.io/openapi.json
```

### Using Docker

```console
$ docker pull schemathesis/schemathesis:stable
$ docker run schemathesis/schemathesis:stable --version
```

Verify your installation:

```console
$ schemathesis --version
# or using the shorter command alias
$ st --version
Schemathesis 4.0.0
```

## Testing a Sample API

Let's run a basic test against an example API:

```console
$ st run https://example.schemathesis.io/openapi.json
```

This single command:

- Loads the API schema
- Generates test cases based on schema definitions
- Sends requests to test endpoints
- Validates responses with various checks

You should see output similar to this:

```
Schemathesis 4.0.0
━━━━━━━━━━━━━━━━

 ✅  Loaded specification from https://schemathesis.io/openapi.json (in 0.32s)

     Base URL:         http://127.0.0.1/api
     Specification:    Open API 3.0.2
     Operations:       1 selected / 1 total

 ✅  API capabilities:

     Supports NULL byte in headers:    ✘

 ⏭   Examples (in 0.00s)

     ⏭  1 skipped

 ❌  Coverage (in 0.00s)

     ❌ 1 failed

 ❌  Fuzzing (in 0.00s)

     ❌ 1 failed

=================================== FAILURES ===================================
_________________________________ GET /success _________________________________
1. Test Case ID: <PLACEHOLDER>

- Response violates schema

    {} is not of type 'integer'

    Schema:

        {
            "type": "integer"
        }

    Value:

        {}

- Missing Content-Type header

    The following media types are documented in the schema:
    - `application/json`

[200] OK:

    `{}`

Reproduce with:

    curl -X GET http://127.0.0.1/api/success

=================================== SUMMARY ====================================

API Operations:
  Selected: 1/1
  Tested: 1

Test Phases:
  ✅ API probing
  ⏭  Examples
  ❌ Coverage
  ❌ Fuzzing
  ⏭  Stateful (not applicable)

Failures:
  ❌ Response violates schema: 1
  ❌ Missing Content-Type header: 1

Test cases:
  N generated, N found N unique failures

Seed: 42

============================= 2 failures in 1.00s ==============================
```

## Understanding Test Results

In this test:

1. Schemathesis found that the API endpoint's response didn't match what was defined in the schema
2. The endpoint was supposed to return an integer, but returned an empty object (`{}`) instead
3. The response was also missing a required Content-Type header

For each issue found, Schemathesis provides:

- An explanation of what went wrong
- The expected vs. actual values
- A curl command to help you reproduce the issue

## Common Issues and Troubleshooting

Schemathesis identifies these common categories of API problems:

### API Contract Violations

- **Schema violations**: Response bodies that don't match the defined schema
- **Status code issues**: Unexpected or undocumented HTTP status codes
- **Header problems**: Missing or incorrect response headers

### Implementation Flaws

- **Server errors**: 5xx responses indicating server-side problems
- **Data validation issues**: APIs accepting invalid data or rejecting valid data
- **Security concerns**: Potential authentication bypasses

### Stateful Behavior Issues

- **Resource state problems**: Resources inaccessible after creation or accessible after deletion

When you encounter these issues:

1. Use the provided curl command to reproduce and verify the problem
2. Check your API implementation against the schema definition
3. Determine if the issue is in the schema (incorrect definition) or the API (incorrect implementation)
4. For schema issues, update your schema definition
5. For API issues, modify your implementation to comply with the schema

## Testing Your Own API

To test your own API:

1. Make sure your API is running
2. Run Schemathesis against your schema:

```console
# If your API serves its own schema
$ st run https://your-api.com/openapi.json

# If you have a local schema file
$ st run ./openapi.yaml --url https://your-api.com
```

That's it! Schemathesis will automatically generate test cases based on your schema and identify any server errors or compliance issues.

## Where to Go Next

- [Core Concepts](core-concepts.md) - Understand how Schemathesis works
- [Command-Line Interface](using/cli.md) - Learn all available CLI options
- [Authentication](using/configuration.md#authentication) - Configure tests for protected APIs
- [Continuous Integration](ci/overview.md) - Automate API testing in your CI pipeline
