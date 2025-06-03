# Schemathesis

> **Catch API bugs before your users do.** 

Schemathesis automatically generates thousands of test cases from your OpenAPI or GraphQL schema and finds the edge cases that break your API.

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

**Bugs that cause real problems**:

- Registration forms that crash on legitimate international names
- Shopping carts accepting negative quantities or invalid product IDs
- User profiles returning incomplete data that breaks mobile apps
- APIs that fail silently instead of showing proper error messages

## Why developers choose Schemathesis

Immediate results:

 - **🎯 Find 5-15 real bugs** in a typical API within the first test run

 - **⏱️ 1-minute setup** - Just point it at your OpenAPI schema

 - **🔄 Zero maintenance** - Automatically tests new endpoints as you add them

Easy integration:

 - **🔌 Works with existing tools** - Integrates with pytest and CI/CD

 - **📑 Uses your existing docs** - Reads OpenAPI/GraphQL schemas you already have

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

## Need help?

* **[Resources](resources.md)** — Community articles, videos, and tutorials
* **[FAQ](faq.md)** — Frequently Asked Questions
