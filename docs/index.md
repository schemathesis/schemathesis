# Schemathesis

Schemathesis automatically generates and runs API tests from your OpenAPI or GraphQL schema to find bugs and spec violations.

<p align="center">
  <img src="https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/demo.gif" alt="Schemathesis automatically finding a server error"/>
  <br>
  <i>Automatically finding specification violations and server errors</i>
</p>

## Why Schemathesis?

- 📑 **Schema-Based Testing** - Transform API documentation into a comprehensive test suite
- 🚀 **Zero Configuration** - Begin testing immediately with a valid OpenAPI or GraphQL schema
- ⚙️ **CI-Ready** - Integrate API testing into existing pipelines without complex configuration
- 🛡️ **Effective Coverage** - Find edge cases no manual testing could uncover
- 🔬 **Research-Backed**: [Recognized](https://dl.acm.org/doi/10.1145/3617175) in [academic research](https://ieeexplore.ieee.org/document/9793781) as a state-of-the-art API testing tool


<div class="testimonial-highlight">
  <blockquote>
    "The tool is amazing as it can test negative scenarios instead of me and much faster!"
  </blockquote>
  <cite>— Luděk Nový, JetBrains</cite>
</div>

## Try It

```console
# Quickest way to start:
$ uvx schemathesis run http://example.schemathesis.io/openapi.json
```

!!! tip ""

    For installing Schemathesis, we recommend using [uv](https://docs.astral.sh/uv/), a fast Python package installer and environment manager.

See [Getting Started](getting-started.md) for installation options and your first test run.

## Documentation

<div class="grid">
  <div class="card">
    <h3><a href="getting-started">Getting Started</a></h3>
    <p>Install Schemathesis and run your first API test in minutes</p>
  </div>
  <div class="card">
    <h3><a href="core-concepts">Core Concepts</a></h3>
    <p>Understand how Schemathesis generates and executes tests</p>
  </div>
  <div class="card">
    <h3><a href="using/cli">CLI Guide</a></h3>
    <p>Learn how to use Schemathesis from the command line</p>
  </div>
  <div class="card">
    <h3><a href="using/python-integration">Python Integration</a></h3>
    <p>Embed API testing directly in your Python test suites</p>
  </div>
</div>

## Schema Support

- **OpenAPI**: 2.0 (Swagger), 3.0, and 3.1
- **GraphQL**: 2018 specification

## Learn More

* **[Using Schemathesis](using/cli.md)** — Practical guides for running tests with different interfaces
    * [Command-Line Interface](using/cli.md)
    * [Python Integration](using/python-integration.md)
    * [Continuous Integration](ci/overview.md)
    * [Configuration](using/configuration.md)

* **[Extending Schemathesis](extending/overview.md)** — Customize and enhance testing capabilities
    * [Custom Checks](extending/checks.md)
    * [Data Generation](extending/data-generation.md)
    * [Authentication](extending/auth.md)
    * [Hooks](extending/hooks.md)

* **[Reference](reference/configuration.md)** — Comprehensive documentation of all options and settings
    * [Configuration Options](reference/configuration.md)
    * [CLI Options](reference/cli.md)
    * [Checks](reference/checks.md)
    * [Reporting](reference/reporting.md)

* **[Resources](resources.md)** — Community articles, videos, and tutorials

* **[Troubleshooting](troubleshooting.md)** — Solve common issues
