# Frequently Asked Questions

## What kind of data does Schemathesis generate?

Schemathesis generates three types of data:

- **Schema examples** from your API documentation
- **Valid test data** that follows schema constraints
- **Invalid test data** that deliberately breaks constraints

The data covers all JSON Schema types for OpenAPI and valid queries for GraphQL.

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
- **Python library**: Integrates with pytest test suites but has fewer features than the CLI.

## What if my application doesn't have an API schema?

If your API doesn't have a schema, you have several options:

1. **Generate a schema**: Use tools like [flasgger](https://github.com/flasgger/flasgger) (Python), [GrapeSwagger](https://github.com/ruby-grape/grape-swagger) (Ruby), or [Swashbuckle](https://github.com/domaindrivendev/Swashbuckle.AspNetCore) (ASP.NET) to automatically generate an initial schema from your code.

2. **Write a minimal schema**: Create a basic schema manually covering just the endpoints you want to test first, then expand it over time.

3. **Use schema inference tools**: Some third-party tools can observe API traffic and generate a schema based on observed requests and responses.

Starting with an imperfect schema is fine - Schemathesis can help you refine it by identifying inconsistencies between your schema and implementation.

## How long does it usually take for Schemathesis to test an API?

**Usually 30 seconds to 5 minutes**, depending on:

- API complexity (number of endpoints and parameters)
- Test configuration (`--max-examples` setting)
- API response time
- Schema complexity

Control duration with `--max-examples` and `--workers` options.

## How is Schemathesis different from other API testing tools?

Schemathesis differs from other API testing tools in several ways:

- **Property-based testing**: Tests API properties (like "all responses should match their schema") rather than specific input-output pairs, automatically exploring the input space to find violations.

- **Stateful testing**: Schemathesis can test sequences of API calls to find issues that only appear in specific request orders.

- **Failure minimization**: When issues are found, Schemathesis automatically simplifies the failing test case to the minimal example that reproduces the problem.

- **Schema-first workflow**: While tools like Postman or Insomnia focus on manual request creation, Schemathesis derives all test cases directly from your API specification.

Compared to tools like Dredd, Schemathesis focuses more on finding unexpected edge cases through property-based testing rather than verifying documented examples.

## What are the limitations of Schemathesis?

Schemathesis has the following limitations:

### GraphQL Limitations

- **Negative Testing:**  
  Schemathesis does not support generating invalid inputs for GraphQL endpoints. The `--mode negative` and `--mode all` options are applicable only to OpenAPI schemas.

If you encounter issues not listed here, please report them on our [GitHub issues page](https://github.com/schemathesis/schemathesis/issues).

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

## Can I use Schemathesis with Allure?

Yes, through JUnit XML export. Allure can generate rich visual reports from Schemathesis test results.

```bash
# 1. Run Schemathesis with JUnit output  
schemathesis run your_schema.yaml --report-junit-path=results.xml

# 2. Set up Allure directory and move results
mkdir allure-results
mv results.xml allure-results/

# 3. Generate and view Allure report
allure generate allure-results --clean
allure open
```

!!! note "Prerequisites"
    Install Allure from [their website](https://allurereport.org/docs/install/){target=_blank}
