# Schemathesis

Schemathesis automatically generates and runs API tests from your OpenAPI or GraphQL schema to find bugs and spec violations.

<p align="center">
  <img src="https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/demo.gif" alt="Schemathesis automatically finding a server error"/>
  <br>
  <i>Automatically finding specification violations and server errors</i>
</p>

<div align="center" markdown>
[Get Your API tested in 5 Minutes :octicons-rocket-24:](quick-start.md){ .md-button .md-button--primary }
</div>

## Why Schemathesis?

- 📑 **Schema-Based Testing** - Transform API documentation into a comprehensive test suite
- 🚀 **Zero Configuration** - Begin testing immediately with a valid OpenAPI or GraphQL schema
- ⚙️ **CI-Ready** - Integrate API testing into existing pipelines without complex configuration
- 🛡️ **Effective Coverage** - Find edge cases no manual testing could uncover
- 🔬 **Research-Backed**: [Recognized](https://dl.acm.org/doi/10.1145/3617175) in [academic research](https://ieeexplore.ieee.org/document/9793781) as a state-of-the-art API testing tool

---

<div class="testimonial-highlight">
  <blockquote>
    "The tool is amazing as it can test negative scenarios instead of me and much faster!"
  </blockquote>
  <cite>— Luděk Nový, JetBrains</cite>
</div>

## Try It

```console
$ uvx schemathesis run http://example.schemathesis.io/openapi.json
```

!!! tip ""

    For installing Schemathesis, we recommend using [uv](https://docs.astral.sh/uv/), a fast Python package installer and environment manager.

## Schema Support

- **OpenAPI**: 2.0 (Swagger), 3.0, and 3.1
- **GraphQL**: 2018 specification

## Documentation

<div class="grid cards" markdown>

-   :material-book-open-page-variant:{ .lg .middle style="color: #2196F3" } __Tutorial__

    ---

    Introduction to Schemathesis:

    - [:octicons-arrow-right-24: Quick Start Guide](quick-start.md)
    - [:octicons-arrow-right-24: Tutorial for CLI & pytest](quick-start.md)

-   :material-puzzle:{ .lg .middle style="color: #4CAF50" } __How-To Guides__

    ---

    Practical guides for using Schemathesis:

     - [:octicons-arrow-right-24: CI / CD Integration](guides/cicd.md)
     - [:octicons-arrow-right-24: Extending Schemathesis](guides/extending.md)
     - [:octicons-arrow-right-24: More...](guides/index.md)

-   :material-puzzle:{ .lg .middle style="color: #9C27B0" } __Explanations__

    ---

    Diving deep into how Schemathesis works:

     - [:octicons-arrow-right-24: Testing Workflow](explanations/workflow.md)
     - [:octicons-arrow-right-24: Data Generation](explanations/data-generation.md)
     - [:octicons-arrow-right-24: Checks](explanations/checks.md)

-   :material-file-document-outline:{ .lg .middle style="color: #FF9800" } __API Reference__

    ---

    Technical API reference:

     - [:octicons-arrow-right-24: Command-Line Interface](reference/cli.md)
     - [:octicons-arrow-right-24: Python API](reference/python.md)
     - [:octicons-arrow-right-24: Configuration File](reference/configuration.md)

</div>

## Learn More

* **[Resources](resources.md)** — Community articles, videos, and tutorials

* **[Troubleshooting](troubleshooting.md)** — Solve common issues
