<p align="center">
    <em>Automate your API Testing: catch crashes, validate specs, and save time</em>
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

**Chat**: <a href="https://discord.gg/R9ASRAmHnA" target="_blank">https://discord.gg/R9ASRAmHnA </a> - active community support available

---

## Why Schemathesis?

Schemathesis focuses on automating your API testing to catch crashes and spec violations. Built on top of the widely-used <a href="https://hypothesis.works/" target="_blank">Hypothesis</a> framework for property-based testing, it offers the following advantages:

🕒 **Time-Saving**

Automatically generates test cases, freeing you from manual test writing.

🔍 **Comprehensive**

Utilizes fuzzing techniques to probe both common and edge-case scenarios, including those you might overlook.

🛠️ **Flexible**

Supports OpenAPI, GraphQL, and can work even with partially complete schemas. Only the parts describing data generation or responses are required.

🎛️ **Customizable**

Customize the framework by writing Python extensions to modify almost any aspect of the testing process.

🔄 **Reproducible**

Generates code samples to help you quickly replicate and investigate any failing test cases.

## Quick Demo

![image](https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/demo.gif)

## Getting Started

Choose from multiple ways to start testing your API with Schemathesis.

> 💡 Your API schema can be either a URL or a local path to a JSON/YAML file.

### 💻 Command-Line Interface

Quick and easy for those who prefer the command line.

**Python**

1. Install via pip: `python -m pip install schemathesis`
2. Run tests

```bash
st run --checks all https://example.schemathesis.io/openapi.json
```

**Docker**

1. Pull Docker image: `docker pull schemathesis/schemathesis:stable`
2. Run tests

```bash
docker run schemathesis/schemathesis:stable
   run --checks all https://example.schemathesis.io/openapi.json
```

### 🐍 Python Library

For more control and customization, integrate Schemathesis into your Python codebase.

1. Install via pip: `python -m pip install schemathesis`
2. Add to your tests:

```python
import schemathesis

schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")


@schema.parametrize()
def test_api(case):
    case.call_and_validate()
```

> 💡 See a complete working example project in the [/example](https://github.com/schemathesis/schemathesis/tree/master/example) directory.

### :octocat: GitHub Integration

**GitHub Actions**

Run Schemathesis tests as a part of your CI/CD pipeline.

Add this YAML configuration to your GitHub Actions:

```yaml
api-tests:
  runs-on: ubuntu-20.04
  steps:
    - uses: schemathesis/action@v1
      with:
        schema: "https://example.schemathesis.io/openapi.json"
        # OPTIONAL. Add Schemathesis.io token for pull request reports
        token: ${{ secrets.SCHEMATHESIS_TOKEN }}
```

For more details, check out our [GitHub Action](https://github.com/schemathesis/action) repository.

**GitHub App**

Receive automatic comments in your pull requests and updates on GitHub checks status. Requires usage of our SaaS platform.

1. Install the [GitHub app](https://github.com/apps/schemathesis).
2. Enable in your repository settings.

### Software as a Service

If you prefer an all-in-one solution with quick setup, we have a [free tier](https://schemathesis.io/#pricing) available.

## How it works

Here’s a simplified overview of how Schemathesis operates:

1. **Test Generation**: Using the API schema to create a test generator that you can fine-tune to your testing requirements.
2. **Execution and Adaptation**: Sending tests to the API and adapting through statistical models and heuristics to optimize subsequent cases based on responses.
3. **Analysis and Minimization**: Checking responses to identify issues. Minimizing means simplifying failing test cases for easier debugging.
4. **Stateful Testing**: Running multistep tests to assess API operations in both isolated and integrated scenarios.
5. **Reporting**: Generating detailed reports with insights and cURL commands for easy issue reproduction.

### Research Findings on Open-Source API Testing Tools

Our study, presented at the **44th International Conference on Software Engineering**, highlighted Schemathesis's performance:

- **Defect Detection**: identified a total of **755 bugs** in **16 services**, finding between **1.4× to 4.5× more defects** than the second-best tool in each case.

- **High Reliability**: consistently operates seamlessly on any project, ensuring unwavering stability and reliability.

Explore the full paper at https://ieeexplore.ieee.org/document/9793781 or pre-print at https://arxiv.org/abs/2112.10328

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

We welcome contributions in code and are especially interested in learning about your use cases.
Understanding how you use Schemathesis helps us extend its capabilities to better meet your needs.

Feel free to discuss ideas and questions through [GitHub issues](https://github.com/schemathesis/schemathesis/issues) or on our [Discord channel](https://discord.gg/R9ASRAmHnA).
For more details on how to contribute, see our [contributing guidelines](https://github.com/schemathesis/schemathesis/blob/master/CONTRIBUTING.rst).

## Let's make it better together 🤝

Your feedback is essential for improving Schemathesis.
By sharing your thoughts, you help us develop features that meet your needs and expedite bug fixes.

1. **Why Give Feedback**: Your input directly influences future updates, making the tool more effective for you.
2. **How to Provide Feedback**: Use [this form](<(https://forms.gle/kJ4hSxc1Yp6Ga96t5)>) to share your experience.
3. **Data Privacy**: We value your privacy. All data is kept confidential and may be used in anonymized form to improve our test suite and documentation.

Thank you for contributing to making Schemathesis better! 👍

## Commercial support

If you're a large enterprise or startup seeking specialized assistance, we offer commercial support to help you integrate Schemathesis effectively into your workflows.
This includes:

- Quicker response time for your queries.
- Direct consultation to work closely with your API specification, optimizing the Schemathesis setup for your specific needs.

To discuss a custom support arrangement that best suits your organization, please contact our support team at <a href="mailto:support@schemathesis.io">support@schemathesis.io</a>.

## Additional content

### Papers

- [Deriving Semantics-Aware Fuzzers from Web API Schemas](https://ieeexplore.ieee.org/document/9793781) by **@Zac-HD** and **@Stranger6667**
  - **Description**: Explores the automation of API testing through semantics-aware fuzzing. Presented at ICSE 2022.
  - **Date**: 20 Dec 2021

### Articles

- [Auto-Generating & Validating OpenAPI Docs in Rust: A Streamlined Approach with Utoipa and Schemathesis](https://identeco.de/en/blog/generating_and_validating_openapi_docs_in_rust/) by **identeco**
  - **Description**: Demonstrates OpenAPI doc generation with Utoipa and validating it with Schemathesis.
  - **Date**: 01 Jun 2023
- [Testing APIFlask with schemathesis](http://blog.pamelafox.org/2023/02/testing-apiflask-with-schemathesis.html) by **@pamelafox**
  - **Description**: Explains how to test APIFlask applications using Schemathesis.
  - **Date**: 27 Feb 2023
- [Using Hypothesis and Schemathesis to Test FastAPI](https://testdriven.io/blog/fastapi-hypothesis/) by **@amalshaji**
  - **Description**: Discusses property-based testing in FastAPI with Hypothesis and Schemathesis.
  - **Date**: 06 Sep 2022
- [How to use Schemathesis to test Flask API in GitHub Actions](https://notes.lina-is-here.com/2022/08/04/schemathesis-docker-compose.html) by **@lina-is-here**
  - **Description**: Guides you through setting up Schemathesis with Flask API in GitHub Actions.
  - **Date**: 04 Aug 2022
- [Using API schemas for property-based testing](https://habr.com/ru/company/oleg-bunin/blog/576496/) (RUS) about Schemathesis by **@Stranger6667**
  - **Description**: Covers the usage of Schemathesis for property-based API testing.
  - **Date**: 07 Sep 2021
- [Schemathesis: property-based testing for API schemas](https://dygalo.dev/blog/schemathesis-property-based-testing-for-api-schemas/) by **@Stranger6667**
  - **Description**: Introduces property-based testing for OpenAPI schemas using Schemathesis.
  - **Date**: 26 Nov 2019

### Videos

- [Schemathesis tutorial](https://appdev.consulting.redhat.com/tracks/contract-first/automated-testing-with-schemathesis.html) with an accompanying [video](https://www.youtube.com/watch?v=4r7OC-lBKMg) by **Red Hat**
  - **Description**: Provides a hands-on tutorial for API testing with Schemathesis.
  - **Date**: 09 Feb 2023
- [Effective API schemas testing](https://youtu.be/VVLZ25JgjD4) from DevConf.cz by **@Stranger6667**
  - **Description**: Talks about using Schemathesis for property-based API schema testing.
  - **Date**: 24 Mar 2021
- [API-schema-based testing with schemathesis](https://www.youtube.com/watch?v=9FHRwrv-xuQ) from EuroPython 2020 by **@hultner**
  - **Description**: Introduces property-based API testing with Schemathesis.
  - **Date**: 23 Jul 2020

## License

This project is licensed under the terms of the [MIT license](https://opensource.org/licenses/MIT).
