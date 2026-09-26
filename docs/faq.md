# Frequently Asked Questions

## How do I restrict the range of generated values?

Override the strategy for a `format` such as `date` (see [Overriding built-in formats](guides/extending.md#overriding-built-in-formats)), limit the string character set with [`generation.codec`](reference/configuration.md#generationcodec), or disable null bytes with [`generation.allow-x00`](reference/configuration.md#generationallow-x00).

## What kind of data does Schemathesis generate?

Schemathesis generates three types of data:

- **Schema examples** from your API documentation
- **Valid test data** that follows schema constraints
- **Invalid test data** that deliberately breaks constraints

The data covers all JSON Schema types for OpenAPI and both valid and invalid queries for GraphQL.

Note, that some generated data may be rejected by your API if the validation rules are not expressed in your schema.

## What types of API issues can Schemathesis find?

Schemathesis identifies problems in three main categories:

**Schema Compliance Issues**

- Response bodies not matching schemas
- Undocumented status codes
- Missing required headers
- Wrong content types

**Implementation Flaws**

- Server crashes (5xx responses)
- Accepting invalid data
- Rejecting valid data
- Allowing missing required headers
- Authentication bypasses

**Stateful Behavior Issues**

- Deleted resources still accessible
- Created resources not available

See more details in the [Checks reference](reference/checks.md).

## How should I run Schemathesis?

- **CLI**: Complete feature set with all test phases, and reporting. Recommended for most users.
- **Python library**: Integrates with pytest test suites. `@schema.parametrize()` does not run the stateful phase (use [`schema.as_state_machine()`](guides/stateful-testing.md)), does not run API capability probes, and does not apply `--generation-maximize` metrics (see [Targeted Testing](guides/targeted.md#target-a-metric-in-pytest)).

## What if my application doesn't have an API schema?

If your API doesn't have a schema, you have several options:

1. **Generate a schema**: Use tools like [flasgger](https://github.com/flasgger/flasgger) (Python), [GrapeSwagger](https://github.com/ruby-grape/grape-swagger) (Ruby), or [Swashbuckle](https://github.com/domaindrivendev/Swashbuckle.AspNetCore) (ASP.NET) to automatically generate an initial schema from your code.

2. **Write a minimal schema**: Create a basic schema manually covering just the endpoints you want to test first, then expand it over time.

3. **Infer a schema from traffic**: [mitmproxy2swagger](https://github.com/alufers/mitmproxy2swagger) builds an OpenAPI schema from recorded HTTP traffic.

Starting with an imperfect schema is fine - Schemathesis can help you refine it by identifying inconsistencies between your schema and implementation.

## How long does it usually take for Schemathesis to test an API?

It depends on the number of operations and parameters, the API's response time, and the configuration. Control the duration with:

- `--max-examples` - test cases the fuzzing phase generates per operation, and scenarios in the stateful phase
- `--max-time` - a wall-clock budget for the whole run
- `--workers` - operations tested in parallel
- `--phases` - which test phases run

## How is Schemathesis different from other API testing tools?

Schemathesis differs from other API testing tools in several ways:

- **Property-based testing**: Tests API properties (like "all responses should match their schema") rather than specific input-output pairs, automatically exploring the input space to find violations.

- **Stateful testing**: Schemathesis can test sequences of API calls to find issues that only appear in specific request orders.

- **Failure minimization**: When issues are found, Schemathesis automatically simplifies the failing test case to the minimal example that reproduces the problem.

- **Schema-first workflow**: While tools like Postman or Insomnia focus on manual request creation, Schemathesis derives all test cases directly from your API specification.

Compared to tools like Dredd, Schemathesis focuses more on finding unexpected edge cases through property-based testing rather than verifying documented examples.

## Why is Schemathesis skipping my Authorization header?

Schemathesis **intentionally** removes or modifies authentication in some test cases. This is security testing, not a bug.

**Why this happens:**

Schemathesis verifies that your API properly validates authentication by testing with:
- No authentication credentials
- Incorrect authentication credentials

This helps catch authentication bypass vulnerabilities where APIs accept requests they should reject.

**When you'll see this:**

- The `ignored_auth` check makes additional requests without auth or with invalid credentials
- Some test cases in the coverage phase may omit required headers including Authorization
- You'll see failures if your API accepts requests it should reject

!!! important ""
    The majority of test cases still use your provided authentication normally. Only specific security-focused tests intentionally modify it.

## How do I authenticate when my API issues tokens from a login endpoint?

Declare the login endpoint in `schemathesis.toml`; see [Declarative Dynamic Authentication](guides/auth.md#declarative-dynamic-authentication). For tokens that expire mid-run, see [Dynamic Token Authentication](guides/auth.md#dynamic-token-authentication).

## Can I use Schemathesis with Allure?

Yes. Pass `--report-allure-path allure-results` and build the report with the Allure CLI; see the [Allure Integration guide](guides/allure.md). Without the `allure` extra, [export JUnit XML](guides/allure.md#without-the-allure-extra-junit-xml) instead.
