# Frequently Asked Questions

## What kind of data does Schemathesis generate?

Schemathesis generates three types of test data:

- **Explicit examples** directly from your API schema (when available)
- **Valid data** that conforms to your API schema's constraints
- **Invalid data** that deliberately violates schema constraints (when using negative testing modes, currently limited to OpenAPI tests)

For OpenAPI schemas, the data generation covers all JSON Schema data types and their combinations, with varying complexity and edge cases. For GraphQL, Schemathesis generates valid queries based on the schema structure.

While Schemathesis attempts to generate realistic data based on schema constraints, it may occasionally generate data that is rejected by your application due to validation rules not expressed in the schema.

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

Schemathesis can be run in two primary ways:

- **As a CLI tool**: Ideal for testing APIs built in any programming language. The CLI offers the most complete feature set, including all test phases, comprehensive reporting, and stateful testing. This is the recommended approach for most users.
- **As a Python library**: Useful for Python applications where you want to integrate with existing pytest test suites. While the Python API has some limitations compared to the CLI due to pytest constraints, it offers more opportunities for customization and extension.

Choose the CLI approach for the most comprehensive testing capabilities and language-agnostic testing. Use the Python library when you need direct programmatic control or tight integration with your Python testing infrastructure.

## What if my application doesn't have an API schema?

If your API doesn't have a schema, you have several options:

1. **Generate a schema**: Use tools like [flasgger](https://github.com/flasgger/flasgger) (Python), [GrapeSwagger](https://github.com/ruby-grape/grape-swagger) (Ruby), or [Swashbuckle](https://github.com/domaindrivendev/Swashbuckle.AspNetCore) (ASP.NET) to automatically generate an initial schema from your code.

2. **Write a minimal schema**: Create a basic schema manually covering just the endpoints you want to test first, then expand it over time.

3. **Use schema inference tools**: Some third-party tools can observe API traffic and generate a schema based on observed requests and responses.

Starting with an imperfect schema is fine - Schemathesis can help you refine it by identifying inconsistencies between your schema and implementation.

## How long does it usually take for Schemathesis to test an API?

Testing duration depends on several factors:

- **API complexity**: More endpoints and parameters mean more tests
- **Test configuration**: Settings like `--max-examples` directly affect the number of tests generated
- **Response time**: Slower APIs take longer to test
- **Schema complexity**: Complex schemas may require more tests to achieve good coverage

In practice, testing typically takes from a few seconds to a few minutes for most APIs. Very large or complex APIs might take longer, especially with high `--max-examples` settings or when using stateful testing.

You can control testing duration by adjusting the `--max-examples` parameter and by enabling parallel testing with the `--workers` option.

## How is Schemathesis different from other API testing tools?

Schemathesis differs from other API testing tools in several key ways:

- **Property-based testing**: Tests API properties (like "all responses should match their schema") rather than specific input-output pairs, automatically exploring the input space to find violations.

- **Stateful testing**: Schemathesis can test sequences of API calls to find issues that only appear in specific request orders.

- **Failure minimization**: When issues are found, Schemathesis automatically simplifies the failing test case to the minimal example that reproduces the problem.

- **Schema-first workflow**: While tools like Postman or Insomnia focus on manual request creation, Schemathesis derives all test cases directly from your API specification.

Compared to tools like Dredd, Schemathesis focuses more on finding unexpected edge cases through property-based testing rather than verifying documented examples.

## What are the known limitations of Schemathesis?

Schemathesis has the following known limitations:

### Schema Processing Limitations

- **Recursive References:**  
  Schemathesis handles most recursive schemas by cutting recursion at a defined depth. However, in a very small fraction of cases (approximately 25 out of over 100,000 schemas tested), complex recursive patterns involving multiple reference hops may cause errors. For more details, see [GitHub issue #947](https://github.com/schemathesis/schemathesis/issues/947).

### GraphQL Limitations

- **Negative Testing:**  
  Schemathesis does not support generating invalid inputs for GraphQL endpoints. The `--mode negative` and `--mode all` options are applicable only to OpenAPI schemas.

If you encounter issues not listed here, please report them on our [GitHub issues page](https://github.com/schemathesis/schemathesis/issues).
