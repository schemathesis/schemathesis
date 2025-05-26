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

Schemathesis automatically generates thousands of test cases from your OpenAPI or GraphQL schema and finds edge cases that break your API.

<p align="center">
  <img src="https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/demo.gif" alt="Schemathesis automatically finding a server error"/>
  <br>
  <i>Finding bugs that manual testing missed</i>
</p>

## Try it now

```console
# Test a demo API - finds real bugs instantly
uvx schemathesis run https://example.schemathesis.io/openapi.json

# Test your own API
uvx schemathesis run https://your-api.com/openapi.json
```


## What problems does it solve?

- 💥 **500 errors** that crash your API on edge case inputs
- 📋 **Schema violations** where your API returns different data than documented  
- 🚪 **Validation bypasses** where invalid data gets accepted
- 🔗 **Integration failures** when responses don't match client expectations

# Installation & Usage

**Command Line:**
```console
uv pip install schemathesis
schemathesis run https://your-api.com/openapi.json
```

**Python Tests:**
```python
import schemathesis

schema = schemathesis.openapi.from_url("https://your-api.com/openapi.json")

@schema.parametrize()
def test_api(case):
    case.call_and_validate()  # Finds bugs automatically
```

**CI/CD:**
```yaml
- uses: schemathesis/action@v1
  with:
    schema: "https://your-api.com/openapi.json"
```

## Who uses it

Used by teams at **[Spotify](https://github.com/backstage/backstage)**, **[WordPress](https://github.com/WordPress/openverse)**, **JetBrains**, **Red Hat** and dozens other companies.


> "_Schemathesis is the best tool for fuzz testing of REST API on the market. We are at Red Hat use it for examining our applications in functional and integrations testing levels._" - Dmitry Misharov, RedHat

## Documentation

📚 **[Complete documentation](https://schemathesis.readthedocs.io/)** with guides, examples, and API reference.

## Get Help

- 💬 [Discord community](https://discord.gg/R9ASRAmHnA)
- 🐛 [GitHub issues](https://github.com/schemathesis/schemathesis/issues)  
- ✉️ [Email support](mailto:support@schemathesis.io)

## Contributing

We welcome contributions! See our [contributing guidelines](CONTRIBUTING.rst) and join discussions in [issues](https://github.com/schemathesis/schemathesis/issues) or [Discord](https://discord.gg/R9ASRAmHnA).

## Acknowledgements

Schemathesis is built on top of <a href="https://hypothesis.works/" target="_blank">Hypothesis</a>, a powerful property-based testing library for Python.

---

> **Note:** This is the V4 development branch. For the stable release, see [V3](https://github.com/schemathesis/schemathesis/tree/v3).

## License

This project is licensed under the terms of the [MIT license](https://opensource.org/licenses/MIT).
