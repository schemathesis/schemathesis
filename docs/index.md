# Schemathesis

A command-line tool that tests APIs using their OpenAPI/GraphQL schema.

<p align="center">
  <img src="https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/demo.gif" alt="Schemathesis automatically finding a server crash"/>
  <br>
  <i>Finding specification violations and server errors automatically</i>
</p>

## Why Schemathesis?

- 🔍 **Schema-Based Generation** - Creates test cases directly from your API documentation
- 🛡️ **Zero Configuration** - Works immediately with any valid OpenAPI or GraphQL schema
- 🔄 **Advanced Testing Techniques** - Employs stateful testing, boundary analysis, and fuzzing
- 🧪 **Continuous Testing** - Integrates with CI/CD pipelines for automated verification
- ⚡ **Extensive Coverage** - Tests more scenarios than manual scenarios can reasonably cover


<div class="testimonial-highlight">
  <blockquote>
    "The tool is amazing as it can test negative scenarios instead of me and much faster!"
  </blockquote>
  <cite>— Luděk Nový, JetBrains</cite>
</div>

## Installation & Quick Start

```console
# Try without installing (quickest way to start)
$ uvx schemathesis run http://example.schemathesis.io/openapi.json
```

For regular use, install with `uv pip install`:

```console
$ uv pip install schemathesis

# Also available via Docker
$ docker run schemathesis/schemathesis:stable \
     run http://example.schemathesis.io/openapi.json
```

Works with partially valid schemas - perfect for APIs under development.

## Basic Usage

After installation, Schemathesis operates with minimal configuration:

```console
# Run comprehensive testing with default settings
$ schemathesis run http://example.schemathesis.io/openapi.json

# Verify only the examples in your schema
$ schemathesis run http://example.schemathesis.io/openapi.json --phases=examples

# Run intensive testing with many examples
$ schemathesis run http://example.schemathesis.io/openapi.json --max-examples=10
```

## Types of Issues Detected

Schemathesis identifies problems in three main categories:

#### API Contract Violations

- Responses not matching documented schemas
- Undocumented status codes
- Missing or incorrect content types

#### Implementation Flaws

- Server errors (5xx responses)
- Data validation issues (accepting invalid data or rejecting valid data)
- Authentication bypasses

#### Stateful Behavior Issues

- Resources accessible after deletion
- Resources unavailable after creation

## CI/CD Integration

Add automated API testing to your workflow:

```yaml
# GitHub Actions example
api-tests:
  runs-on: ubuntu-latest
  steps:
    - uses: schemathesis/action@v1
      with:
        schema: "https://example.schemathesis.io/openapi.json"
```

## Schema Support

- OpenAPI 2.0, 3.0, and 3.1
- GraphQL (2018 spec)

## Learn More

- [How Schemathesis Works](./how-it-works.md) - Understanding the testing approach and design principles
- [CLI Usage Guide](./cli-usage-guide.md) - Explore different testing scenarios and CLI capabilities
- [Python & pytest Integration](./python-integration.md) - Using Schemathesis in Python test suites
- [Configuration Guide](./configuration.md) - Customizing test execution for your API
- [CI/CD Guide](./python-integration.md) - Using Schemathesis within CI/CD pipelines
- [Extending Schemathesis](./extending.md) - Writing custom hooks and extensions
- [FAQ](./faq.md) - Common questions and solutions
