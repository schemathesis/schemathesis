<p align="center">
    <em>Discover API-breaking payloads, keep API documentation up-to-date, and increase confidence in your API</em>
</p>

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

---

**Documentation**: <a href="https://schemathesis.readthedocs.io/en/stable/" target="_blank">https://schemathesis.readthedocs.io/en/stable/ </a>

**Chat**: <a href="https://discord.gg/R9ASRAmHnA" target="_blank">https://discord.gg/R9ASRAmHnA </a>

---

Schemathesis is a specification-based testing tool for OpenAPI and GraphQL apps based on the powerful <a href="https://hypothesis.works/" target="_blank">Hypothesis</a> framework.

Here are the key features:

- **OpenAPI & GraphQL**: Test a wide range of APIs with ease, regardless of the specification used.
- **Positive & Negative Tests**: Ensure your API handles valid and invalid inputs, incl. unexpected ones.
- **Stateful Testing**: Automatically generate sequences of API
  requests where subsequent requests build on previous ones for
  testing complex and interdependent scenarios.
- **Session Replay**: Quickly store and replay test sessions to easily investigate and resolve issues.
- **Targeted Testing**: Guide data generation towards specific metrics
  like response time or size. Uncover performance or resource usage
  issues and optimize API behavior under different conditions.
- **Python Integration**: Utilize native ASGI/WSGI support for faster testing your Python applications.
- **Customization**: Tune data generation, API response verification, and testing process to fit your needs.
- **CI Integration**: Run tests on every code change with Docker image
  and [GitHub Action](https://github.com/schemathesis/action).

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

## How does it work?

Schemathesis uses your API's schema to generate both valid and invalid
test scenarios, helping you verify API compliance and catch potential
issues. It also verifies examples from the schema itself.

Schemathesis generates high quality, diverse test data based on novel
techniques like [Swarm
testing](https://dl.acm.org/doi/10.1145/2338965.2336763) or [Schema
fuzzing](https://patricegodefroid.github.io/public_psfiles/fse2020.pdf),
ensuring that your API is thoroughly tested and even the most elusive
bugs are uncovered.

It's a versatile tool that works with any language, as long as you have
an API schema in a supported format.

Learn more about how it works in our [research
paper](https://arxiv.org/abs/2112.10328).

## Why use Schemathesis?

1. **Avoid Crashes**: Discover API-breaking payloads and avoid crashes, database corruption, and hangs.
2. **Keep API Documentation Up-to-Date**: With Schemathesis, you never have to worry about API consumers using outdated specifications or incorrect payload examples.
3. **Easy Debugging**: Schemathesis provides you with a detailed failure report, along with a single cURL command to help you reproduce the problem instantly.
4. **Increased Confidence in API Stability**: By thoroughly testing your API with Schemathesis, you can have peace of mind knowing that your API is functioning as intended.
5. **Thorough Testing Coverage**: Schemathesis generates a large number of scenarios to test your API against, giving you a comprehensive view of its behavior and potential issues.
6. **Time-Saving**: Schemathesis streamlines API testing, saving your time for other tasks.

## Getting started

Schemathesis can be used as a CLI, a Python library, or as a [SaaS](https://schemathesis.io/?utm_source=github).

- **CLI**: Quick and easy way to get started, for those who prefer the command line.
- **Python Library**: More control and customization, for developers integrating with their codebase.
- **SaaS**: No setup or installation, if you prefer an all-in-one solution with great visuals. Free tier included.

## Installation

```bash
python -m pip install schemathesis
```

This command installs the `st` entrypoint.

You can also use our Docker image without installing Schemathesis as a Python package:

```bash
docker pull schemathesis/schemathesis:stable
```

## Example

### Command line

```bash
st run --checks all https://example.schemathesis.io/openapi.json

# Or

docker run schemathesis/schemathesis:stable \
    run --checks all https://example.schemathesis.io/openapi.json
```

![image](https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/demo.gif)

### Python tests

```python
import schemathesis

schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")


@schema.parametrize()
def test_api(case):
    case.call_and_validate()
```

Choose CLI for simplicity or Python package for greater flexibility. Both options run extensive tests and report failures with reproduction instructions.

💡 See a complete working example project in the [/example](https://github.com/schemathesis/schemathesis/tree/master/example) directory.💡

## GitHub Actions

If you use GitHub Actions, there is a native [GitHub app](https://github.com/apps/schemathesis) that reports test results directly to your pull requests.

```yaml
api-tests:
  runs-on: ubuntu-20.04
  steps:
    # Runs Schemathesis tests with all checks enabled
    - uses: schemathesis/action@v1
      with:
        # Your API schema location
        schema: "http://localhost:5000/api/openapi.json"
        # OPTIONAL. Your Schemathesis.io token
        token: ${{ secrets.SCHEMATHESIS_TOKEN }}
```

Check our [GitHub Action](https://github.com/schemathesis/action) for more details.

## Let's make it better together 🤝

We're always looking to make Schemathesis better, and your feedback is
a crucial part of that journey! If you've got a few minutes, we'd love
to hear your thoughts on your experience using Schemathesis.

Just follow [this link](https://forms.gle/kJ4hSxc1Yp6Ga96t5) to let us know what you think 💬

Thanks for helping us make Schemathesis even better! 👍

## Commercial support

For assistance with integrating Schemathesis into your company workflows or improving its effectiveness, reach out to our support team at <a href="mailto:support@schemathesis.io">support@schemathesis.io</a>.
Additionally, we offer commercial support for those looking for extra assurance and priority assistance.

## Contributing

Any contribution to development, testing, or any other area is highly
appreciated and useful to the project. For guidance on how to contribute
to Schemathesis, see the [contributing guidelines](https://github.com/schemathesis/schemathesis/blob/master/CONTRIBUTING.rst).

## Additional content

- [Deriving Semantics-Aware Fuzzers from Web API Schemas](https://arxiv.org/abs/2112.10328) by **@Zac-HD** and **@Stranger6667**
- [An article](https://dygalo.dev/blog/schemathesis-property-based-testing-for-api-schemas/) about Schemathesis by **@Stranger6667**
- [Effective API schemas testing](https://youtu.be/VVLZ25JgjD4) from DevConf.cz by **@Stranger6667**
- [How to use Schemathesis to test Flask API in GitHub Actions](https://notes.lina-is-here.com/2022/08/04/schemathesis-docker-compose.html) by **@lina-is-here**
- [A video](https://www.youtube.com/watch?v=9FHRwrv-xuQ) from EuroPython 2020 by **@hultner**
- [Schemathesis tutorial](https://appdev.consulting.redhat.com/tracks/contract-first/automated-testing-with-schemathesis.html) with an accompanying [video](https://www.youtube.com/watch?v=4r7OC-lBKMg) by Red Hat
- [Using Hypothesis and Schemathesis to Test FastAPI](https://testdriven.io/blog/fastapi-hypothesis/) by **@amalshaji**
- [A tutorial](https://habr.com/ru/company/oleg-bunin/blog/576496/) (RUS) about Schemathesis by **@Stranger6667**

## License

This project is licensed under the terms of the [MIT license](https://opensource.org/licenses/MIT).
