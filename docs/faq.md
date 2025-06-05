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

**API Contract Violations**

- Responses not matching documented schemas
- Undocumented status codes
- Missing or incorrect content types

**Implementation Flaws**

- Server errors (5xx responses)
- Data validation issues (accepting invalid data or rejecting valid data)
- Authentication bypasses

**Stateful Behavior Issues**

- Resources accessible after deletion
- Resources unavailable after creation

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

### Schema Processing Limitations

- **Recursive References:**  
  Schemathesis handles most recursive schemas by cutting recursion at a defined depth. However, in a very small fraction of cases (approximately 25 out of over 100,000 schemas tested), complex recursive patterns involving multiple reference hops may cause errors. For more details, see [GitHub issue #947](https://github.com/schemathesis/schemathesis/issues/947).

### GraphQL Limitations

- **Negative Testing:**  
  Schemathesis does not support generating invalid inputs for GraphQL endpoints. The `--mode negative` and `--mode all` options are applicable only to OpenAPI schemas.

If you encounter issues not listed here, please report them on our [GitHub issues page](https://github.com/schemathesis/schemathesis/issues).
