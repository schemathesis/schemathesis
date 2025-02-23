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

> ⚠️ You are viewing the Schemathesis V4 README (Work in Progress) ⚠️

> This branch is under active development, with substantial changes expected before stabilization. While V4 is fully functional and passing tests, some features are missing, and the documentation may be outdated.

> For the stable release, see the [V3 branch](https://github.com/schemathesis/schemathesis/tree/v3).

> 💡 Have feedback? Share your thoughts in [this discussion](https://github.com/schemathesis/schemathesis/discussions/2677)!

Schemathesis is an API testing tool that automatically finds crashes and validates spec compliance.

<p align="center">
  <img src="https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/demo.gif" alt="Schemathesis Demo"/>
</p>

<p align="center">
  <i>Finding server crashes in the Demo API.</i>
</p>

### Highlights

- 🎯 **Catches Hard-to-Find Bugs**: Automatically uncover crashes and spec violations that manual testing might miss.

- ⚡ **Accelerates Testing**: Generate a wide range of test cases directly from your API schema.

- 🧩 **Integrates Seamlessly**: Works with popular API formats such as OpenAPI and GraphQL, and easily integrates into your existing CI/CD workflows.

- 🔧 **Customizable and Extendable**: Leverage Python extensions to configure and extend your test generation.

- 🐞 **Simplifies Debugging**: Detailed reports and reproducible test cases with cURL commands streamline troubleshooting.

- 🔬 **Proven by Research**: Validated through academic studies on API testing automation, featured in the [ICSE 2022 paper](https://ieeexplore.ieee.org/document/9793781) on semantics-aware fuzzing, and recognized in an [ACM survey](https://dl.acm.org/doi/10.1145/3617175) as a state-of-the-art RESTful API testing tool.

## Installation

Use Schemathesis via Docker, or install it from [PyPI](https://pypi.org/project/schemathesis/)

```console
# Via Docker.
$ docker pull schemathesis/schemathesis:stable

# With pip.
$ pip install schemathesis
```

## Getting Started

Schemathesis works as a standalone CLI:

```console
docker run schemathesis/schemathesis:stable
   run --checks all https://example.schemathesis.io/openapi.json
# Or when installed with pip
schemathesis run --checks all https://example.schemathesis.io/openapi.json
```

Or a Python library:

```python
import schemathesis

schema = schemathesis.openapi.from_url("https://example.schemathesis.io/openapi.json")


@schema.parametrize()
def test_api(case):
    case.call_and_validate()
```

See a complete working example project in the [/example](https://github.com/schemathesis/schemathesis/tree/master/example) directory.

Schemathesis can be easily integrated into your CI/CD pipeline using GitHub Actions. Add this block to your GitHub Actions to run Schemathesis against your API:

```yaml
api-tests:
  runs-on: ubuntu-latest
  steps:
    - uses: schemathesis/action@v1
      with:
        schema: "https://example.schemathesis.io/openapi.json"
```

For more details, check out our [GitHub Action](https://github.com/schemathesis/action) repository or see our [GitHub Tutorial](https://docs.schemathesis.io/tutorials/github).

## Who's Using Schemathesis?

Schemathesis is used by a number of projects and companies, including direct usage or integration into other tools:

- Abstract Machines ([Magistrala](https://github.com/absmach/magistrala))
- Bundesstelle für Open Data ([smard-api](https://github.com/bundesAPI/smard-api))
- [CheckMK](https://github.com/Checkmk/checkmk)
- Chronosphere.io ([Calyptia](https://github.com/chronosphereio/calyptia-api))
- HXSecurity ([DongTai](https://github.com/HXSecurity/DongTai))
- Netflix ([Dispatch](https://github.com/Netflix/dispatch))
- [Pixie](https://github.com/pixie-io/pixie)
- [Qdrant](https://github.com/qdrant/qdrant)
- Spotify ([Backstage](https://github.com/backstage/backstage))
- [Weechat](https://github.com/weechat/weechat)
- WordPress ([OpenVerse](https://github.com/WordPress/openverse))

## Testimonials

"_The world needs modern, spec-based API tests, so we can deliver APIs as-designed. Schemathesis is the right tool for that job._"

<div>Emmanuel Paraskakis - <strong>Level 250</strong></div>

---

"_Schemathesis is the only sane way to thoroughly test an API._"

<div>Zdenek Nemec - <strong>superface.ai</strong></div>

---

"_The tool is absolutely amazing as it can do the negative scenario testing instead of me and much faster! Before I was doing the same tests in Postman client. But it's much slower and brings maintenance burden._"

<div>Luděk Nový - <strong>JetBrains</strong></div>

---

"_Schemathesis is the best tool for fuzz testing of REST API on the market. We are at Red Hat use it for examining our applications in functional and integrations testing levels._"

<div>Dmitry Misharov - <strong>RedHat</strong></div>

---

"_There are different levels of usability and documentation quality among these tools which have been reported, where Schemathesis clearly stands out among the most user-friendly and industry-strength tools._"

<div>Testing RESTful APIs: A Survey - <strong>a research paper by Golmohammadi, at al</strong></div>

---

## Contributing

We welcome contributions in code and are especially interested in learning about your use cases. Your input is essential for improving Schemathesis and directly influences future updates.

### How to Contribute

1. Discuss ideas and questions through [GitHub issues](https://github.com/schemathesis/schemathesis/issues) or on our [Discord channel](https://discord.gg/R9ASRAmHnA).
2. For code contributions, see our [contributing guidelines](https://github.com/schemathesis/schemathesis/blob/master/CONTRIBUTING.rst).
3. Share your experience and thoughts using [this feedback form](https://forms.gle/kJ4hSxc1Yp6Ga96t5).

### Why Your Input Matters

- Enables us to develop useful features and fix bugs faster
- Improves our test suite and documentation

Thank you for contributing to making Schemathesis better! 👍

## Get in Touch

If you need assistance with integrating Schemathesis into your workflows or have specific questions, feel free to reach out at <a href="mailto:support@schemathesis.io">support@schemathesis.io</a>.

## Acknowledgements

Schemathesis is built on top of <a href="https://hypothesis.works/" target="_blank">Hypothesis</a>, a powerful property-based testing library for Python.

## License

This project is licensed under the terms of the [MIT license](https://opensource.org/licenses/MIT).
