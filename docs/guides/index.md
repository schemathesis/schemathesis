# Available Guides

## Getting Started

### [Triaging Failures](triage.md)
Work through a large number of failures systematically — from easy batch fixes to individual investigation.

### [Optimizing for Maximum Bug Detection](config-optimization.md)
Configure Schemathesis for thorough testing when preparing for production releases or conducting security assessments.

### [Authentication](auth.md)
Set up authentication for APIs that require credentials. Covers static tokens, config-based token fetch, dynamic refresh, and endpoint-specific auth strategies.

### [Adding Schema Validation to Existing Tests](schema-conformance.md)
Validate API responses against your schema in existing test suites without using Schemathesis for data generation.

## Integration

### [CI/CD Integration](cicd.md)
Integrate Schemathesis into automated testing pipelines. Includes GitHub Actions, GitLab CI configurations, and reporting best practices.

### [Using Schemathesis with Docker](docker.md)
Run Schemathesis without installing Python — covers file schemas, hooks, and reports.

### [Schema Coverage](coverage.md)
Measure schema-level API coverage down to individual keywords using TraceCov.

### [Testing Python Web Applications](python-apps.md)
Test Flask, FastAPI, and other Python web apps directly without network overhead.

## Customization

### [Custom Media Types](custom-media-types.md)
Generate valid binary content like PDFs or images when your API accepts specialized file formats.

### [Custom Serializers](custom-serializers.md)
Transform test data into non-JSON formats like CSV, MessagePack, or XML for APIs that expect alternative serialization.

### [Custom Response Deserializers](custom-response-deserializers.md)
Decode non-JSON or vendor-specific responses back into Python objects so schema checks work with your API.

### [Server-Sent Events](server-sent-events.md)
Test `text/event-stream` endpoints - validate each event against `itemSchema`, handle polymorphic event types, and work with embedded JSON payloads.

### [GraphQL Custom Scalars](graphql-custom-scalars.md)
Configure domain-specific scalar types so Schemathesis generates appropriate test data for emails, phone numbers, or custom IDs.

### [Extending Schemathesis](extending.md)
Customize data generation and validation through hooks, custom checks, and format strategies.

### [Extending CLI](extending-cli.md)
Add custom command-line options and event handlers for integration with external tools.

## Advanced Testing

### [Customizing Stateful Testing](stateful-testing.md)
Configure authentication, data initialization, and scenario setup for stateful API testing workflows.

### [Using Hypothesis Strategies](hypothesis-strategies.md)
Combine Schemathesis with custom Hypothesis strategies or use Schemathesis strategies in other testing frameworks.

### [Targeted Testing](targeted.md)
Use property-based testing strategies to find performance issues and edge cases by directing test generation toward specific goals.
