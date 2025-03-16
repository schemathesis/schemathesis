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

Schemathesis automatically generates and runs API tests from your OpenAPI or GraphQL schema to find bugs and spec violations.

<p align="center">
  <img src="https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/demo.gif" alt="Schemathesis automatically finding a server error"/>
  <br>
  <i>Automatically finding specification violations and server errors</i>
</p>

> **Note:** This is the V4 branch under active development. While fully functional and passing tests, some features may be missing, and documentation is being updated. For the stable release, see the [V3 branch](https://github.com/schemathesis/schemathesis/tree/v3).

## Why Schemathesis?

- 📑 **Schema-Based Testing** - Transform API documentation into a comprehensive test suite
- 🚀 **Zero Configuration** - Begin testing immediately with a valid OpenAPI or GraphQL schema
- ⚙️ **CI-Ready** - Integrate API testing into existing pipelines without complex configuration
- 🛡️ **Effective Coverage** - Find edge cases no manual testing could uncover
- 🔬 **Research-Backed**: [Recognized](https://dl.acm.org/doi/10.1145/3617175) in [academic research](https://ieeexplore.ieee.org/document/9793781) as a state-of-the-art API testing tool

## Installation

```console
# Using uv (recommended)
$ uv pip install schemathesis

# Using pip
$ pip install schemathesis

# Using Docker
$ docker pull schemathesis/schemathesis:stable
```

## Usage

### Command Line

```console
# Run tests against a schema URL
$ st run https://example.schemathesis.io/openapi.json
```

### Python Library

```python
import schemathesis

schema = schemathesis.openapi.from_url("https://example.schemathesis.io/openapi.json")


@schema.parametrize()
def test_api(case):
    case.call_and_validate()
```

### CI/CD Integration

```yaml
# GitHub Actions example
steps:
  - uses: schemathesis/action@v1
    with:
      schema: "https://example.schemathesis.io/openapi.json"
```

## Documentation

📚 **[Read the full documentation](https://schemathesis.readthedocs.io/)** for guides, examples, and reference material.

## Who's Using Schemathesis?

Schemathesis is used by companies and open-source projects including:

- Netflix ([Dispatch](https://github.com/Netflix/dispatch))
- Spotify ([Backstage](https://github.com/backstage/backstage))
- WordPress ([OpenVerse](https://github.com/WordPress/openverse))
- Chronosphere.io ([Calyptia](https://github.com/chronosphereio/calyptia-api))
- [Qdrant](https://github.com/qdrant/qdrant)
- [Pixie](https://github.com/pixie-io/pixie)
- [CheckMK](https://github.com/Checkmk/checkmk)
- [Weechat](https://github.com/weechat/weechat)
- HXSecurity ([DongTai](https://github.com/HXSecurity/DongTai))
- Abstract Machines ([Magistrala](https://github.com/absmach/magistrala))
- Bundesstelle für Open Data ([smard-api](https://github.com/bundesAPI/smard-api))

## Testimonials

"_The world needs modern, spec-based API tests, so we can deliver APIs as-designed. Schemathesis is the right tool for that job._"

<div>Emmanuel Paraskakis - <strong>Level 250</strong></div>

---

"_Schemathesis is the only sane way to thoroughly test an API._"

<div>Zdenek Nemec - <strong>superface.ai</strong></div>

---

"_The tool is amazing as it can test negative scenarios instead of me and much faster!_"

<div>Luděk Nový - <strong>JetBrains</strong></div>

---

"_Schemathesis is the best tool for fuzz testing of REST API on the market. We are at Red Hat use it for examining our applications in functional and integrations testing levels._"

<div>Dmitry Misharov - <strong>RedHat</strong></div>

---

"_There are different levels of usability and documentation quality among these tools which have been reported, where Schemathesis clearly stands out among the most user-friendly and industry-strength tools._"

<div>Testing RESTful APIs: A Survey - <strong>a research paper by Golmohammadi, at al</strong></div>

---

## Contributing

We welcome contributions! Your input directly influences Schemathesis development.

- Discuss ideas in [GitHub issues](https://github.com/schemathesis/schemathesis/issues) or our [Discord server](https://discord.gg/R9ASRAmHnA)
- See our [contributing guidelines](https://github.com/schemathesis/schemathesis/blob/master/CONTRIBUTING.rst) for code contributions
- Share your experience using [this feedback form](https://forms.gle/kJ4hSxc1Yp6Ga96t5)

## Get in Touch

Need assistance with integration or have specific questions? Contact us at <a href="mailto:support@schemathesis.io">support@schemathesis.io</a>.

## Acknowledgements

Schemathesis is built on top of <a href="https://hypothesis.works/" target="_blank">Hypothesis</a>, a powerful property-based testing library for Python.

## License

This project is licensed under the terms of the [MIT license](https://opensource.org/licenses/MIT).
