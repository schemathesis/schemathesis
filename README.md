<p align="center">
    <a href="https://github.com/schemathesis/schemathesis/actions" target="_blank">
        <img src="https://github.com/schemathesis/schemathesis/actions/workflows/build.yml/badge.svg" alt="Build">
    </a>
    <a href="https://codecov.io/gh/schemathesis/schemathesis/branch/master" target="_blank">
        <img src="https://codecov.io/gh/schemathesis/schemathesis/branch/master/graph/badge.svg" alt="Coverage">
    </a>
    <a href="https://pypi.org/project/schemathesis/" target="_blank">
        <img src="https://img.shields.io/pypi/v/schemathesis.svg" alt="Version">
    </a>
    <a href="https://pypi.org/project/schemathesis/" target="_blank">
        <img src="https://img.shields.io/pypi/pyversions/schemathesis.svg" alt="Python versions">
    </a>
    <a href="https://discord.gg/R9ASRAmHnA" target="_blank">
        <img src="https://img.shields.io/discord/938139740912369755" alt="Discord">
    </a>
    <a href="https://opensource.org/licenses/MIT" target="_blank">
        <img src="https://img.shields.io/pypi/l/schemathesis.svg" alt="License">
    </a>
</p>

## Schemathesis

> **Catch API bugs before your users do.**

Schemathesis tests OpenAPI and GraphQL APIs by generating inputs from your schema, adapting to server responses, and chaining operations into realistic workflows.

<p align="center">
  <img src="https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/demo.gif" alt="Schemathesis automatically finding a server error"/>
  <br>
  <i>Finding bugs that manual testing missed</i>
</p>

## Try it now

```console
# Test a demo API - finds real bugs in 30 seconds
uvx schemathesis run https://example.schemathesis.io/openapi.json

# Test your own API
uvx schemathesis run https://your-api.com/openapi.json
```


## What problems does it solve?

- 💥 **500 errors** that crash your API on edge case inputs
- 📋 **Schema violations** where your API returns different data than documented
- 🚪 **Validation bypasses** where invalid data gets accepted
- 🔗 **Integration failures** when responses don't match client expectations
- 🔄 **Stateful bugs** where operations work individually but fail in realistic workflows


## What can it do?

- ⚙️ **Config file** — auth, phases, and per-operation overrides in [`schemathesis.toml`](https://schemathesis.readthedocs.io/en/stable/configuration/). No Python.
- 🔐 **Authentication** — static headers, Basic, per-security-scheme credentials, or [custom refresh logic](https://schemathesis.readthedocs.io/en/stable/guides/auth/).
- 🔗 **Stateful testing** — [operation links inferred from your schema](https://schemathesis.readthedocs.io/en/stable/explanations/stateful/), no manual wiring.
- 🧠 **Adaptive testing** — [learns constraints, ids, and auth from responses](https://schemathesis.readthedocs.io/en/stable/explanations/adaptive-testing/), reusing them mid-run.
- ✅ **Custom checks** — [assert your own business rules](https://schemathesis.readthedocs.io/en/stable/guides/extending/#custom-validation-checks) next to the built-in ones.
- 📖 **Fuzz dictionaries** — [mix real ids, wordlists, or LLM-generated payloads](https://schemathesis.readthedocs.io/en/stable/guides/fuzz-dictionary/) into generated data.
- 🐌 **Rate limiting** — cap the request rate, or use `auto` to follow [`Retry-After` on 429](https://schemathesis.readthedocs.io/en/stable/reference/configuration/#rate-limit).
- 📊 **Reports** — [JUnit, VCR, HAR, NDJSON, JSON](https://schemathesis.readthedocs.io/en/stable/reference/configuration/#reporting), and [Allure](https://schemathesis.readthedocs.io/en/stable/guides/allure/).
- 🎯 **Schema coverage** — [keyword-level coverage report](https://schemathesis.readthedocs.io/en/stable/guides/coverage/) showing which constraints your tests exercised.
- 🔁 **Replay & baseline** — [re-run past failures](https://schemathesis.readthedocs.io/en/stable/guides/crash-reproduction/) and [fail CI only on new ones](https://schemathesis.readthedocs.io/en/stable/guides/baseline/).

> ⚠️ **Upgrading from older versions?** Check our [Migration Guide](https://github.com/schemathesis/schemathesis/blob/master/MIGRATION.md) for key changes.

# Installation & Usage

**Command Line:**
```console
uv pip install schemathesis
schemathesis run https://your-api.com/openapi.json
```

**Config file** (`schemathesis.toml`, no Python needed):
```toml
headers = { Authorization = "Bearer ${API_TOKEN}" }
generation.max-examples = 500
rate-limit = "auto"
```

**Python Tests:**
```python
import schemathesis

schema = schemathesis.openapi.from_url("https://your-api.com/openapi.json")


@schema.parametrize()
def test_api(case):
    # Tests with random data, edge cases, and invalid inputs
    case.call_and_validate()


# Stateful testing: Tests workflows like: create user -> get user -> delete user
APIWorkflow = schema.as_state_machine()
# Creates a test class for pytest/unittest
TestAPI = APIWorkflow.TestCase
```

**CI/CD:**
```yaml
- uses: schemathesis/action@v3
  with:
    schema: "https://your-api.com/openapi.json"
```

## Who uses it

Used by teams at **[Spotify](https://github.com/backstage/backstage)**, **[WordPress](https://github.com/WordPress/openverse)**, **JetBrains**, **Red Hat**, and dozens of other companies.


> "_Schemathesis is the best tool for fuzz testing of REST APIs on the market. We at Red Hat use it for examining our applications in functional and integration testing levels._" - Dmitry Misharov, RedHat

## See it in action

🔬 **[Live Benchmarks](https://workbench.schemathesis.io)** showing continuous testing results from real-world APIs:

- Code & API schema coverage achieved
- Issues found with detailed categorization
- Performance across different fuzzing strategies

## Documentation

📚 **[Documentation](https://schemathesis.readthedocs.io/en/stable/)** with guides, examples, and API reference.

## Get Help

- 💬 [Discord community](https://discord.gg/R9ASRAmHnA)
- 🐛 [GitHub issues](https://github.com/schemathesis/schemathesis/issues)

## Contributing

We welcome contributions! See our [contributing guidelines](CONTRIBUTING.md) and join discussions in [issues](https://github.com/schemathesis/schemathesis/issues) or [Discord](https://discord.gg/R9ASRAmHnA).

## Acknowledgements

Schemathesis is built on top of <a href="https://hypothesis.works/" target="_blank">Hypothesis</a>, a powerful property-based testing library for Python.

## License

This project is licensed under the terms of the [MIT license](https://opensource.org/licenses/MIT).
