# Schemathesis

Schemathesis automatically generates property-based tests from your OpenAPI or GraphQL schema and exercises the edge cases that break your API.

<p align="center">
  <img src="https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/demo.gif" alt="Schemathesis automatically finding a server error"/>
  <br>
  <i>Finding a server error that manual testing missed</i>
</p>

<div align="center" markdown>
[Find Bugs in Your API in 5 Minutes :octicons-rocket-24:](quick-start.md){ .md-button .md-button--primary }
</div>

## Try it now

```console
uvx schemathesis run https://example.schemathesis.io/openapi.json
```

This command will immediately find real bugs in a demo API and show you exactly how to reproduce them.

## Why teams choose Schemathesis

- 🎯 Find real bugs on the first run (commonly 5–15 in production schemas).
- ⏱️ Point it at your schema—no per-endpoint tests to maintain.
- 🔄 Keeps pace with the spec; new endpoints are covered automatically.
- 🔌 Exports JUnit, HAR, and integrates with `pytest` or CI/CD.
- 📑 Uses your OpenAPI or GraphQL schema as the single source of truth.

!!! quote "Developer feedback"
    "The tool is amazing as it can test negative scenarios instead of me and much faster!" 
    
    *— Luděk Nový, JetBrains*

## Documentation

<div class="grid cards" markdown>

-   :material-book-open-page-variant:{ .lg .middle style="color: #2196F3" } __New to Schemathesis?__

    ---

    Get started in minutes:

    - [:octicons-arrow-right-24: Quick Start - 5 minutes](quick-start.md)
    - [:octicons-arrow-right-24: CLI Tutorial - 20 minutes](tutorials/cli.md)
    - [:octicons-arrow-right-24: Pytest Tutorial - 15 minutes](tutorials/pytest.md)

-   :material-puzzle:{ .lg .middle style="color: #4CAF50" } __How-To Guides__

    ---

    Practical guides for common scenarios:

     - [:octicons-arrow-right-24: CI/CD Integration](guides/cicd.md)
     - [:octicons-arrow-right-24: Extending Schemathesis](guides/extending.md)
     - [:octicons-arrow-right-24: More...](guides/index.md)

-   :material-puzzle:{ .lg .middle style="color: #9C27B0" } __Want to understand how it works?__

    ---

    Deep dive into concepts:

     - [:octicons-arrow-right-24: Data Generation](explanations/data-generation.md)
     - [:octicons-arrow-right-24: Example Testing](explanations/examples.md)
     - [:octicons-arrow-right-24: Stateful Testing](explanations/stateful.md)

-   :material-file-document-outline:{ .lg .middle style="color: #FF9800" } __Need technical details?__

    ---

    Complete reference:

     - [:octicons-arrow-right-24: Command-Line Interface](reference/cli.md)
     - [:octicons-arrow-right-24: Python API](reference/python.md)
     - [:octicons-arrow-right-24: Configuration File](reference/configuration.md)

</div>


!!! important
    **Upgrading from older versions?** See the [Migration Guide](migration.md) for key changes.

## Need help?

* **[Resources](resources.md)** — Community articles, videos, and tutorials
* **[FAQ](faq.md)** — Frequently Asked Questions
