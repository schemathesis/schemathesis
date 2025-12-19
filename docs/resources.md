# External Resources

This page collects articles, papers, videos, tutorials, and integrations about Schemathesis written by the community. These resources provide additional perspectives, use cases, and implementation examples.

## Integrations

- [TraceCov](https://docs.tracecov.sh) by **@Stranger6667**

    - **Description**: Schema-level API coverage analysis. Measures test coverage at the constraint level (minLength, pattern, enum, etc.) rather than just endpoints.

    - **Documentation**: [docs.tracecov.sh](https://docs.tracecov.sh)

    - **Installation**: `uv pip install tracecov`

- [Robot Framework SchemathesisLibrary](https://github.com/aaltat/robotframework-schemathesis) by **@aaltat**

    - **Description**: Robot Framework integration with Schemathesis. Automatically generates test cases from OpenAPI/GraphQL schemas using DataDriver integration.

    - **Documentation**: [Keyword documentation](https://aaltat.github.io/robotframework-schemathesis/SchemathesisLibrary.html)

    - **Installation**: `pip install robotframework-schemathesislibrary`

## Papers

- [Testing RESTFul APIs: A Survey](https://dl.acm.org/doi/10.1145/3617175) by Golmohammadi, et al.

    - **Description**: Academic review of state-of-the-art API testing tools including Schemathesis.

    - **Date**: 24 Nov 2023

- [Deriving Semantics-Aware Fuzzers from Web API Schemas](https://ieeexplore.ieee.org/document/9793781) by **@Zac-HD** and **@Stranger6667**

    - **Description**: Research paper exploring the automation of API testing through semantics-aware fuzzing. Presented at ICSE 2022.

    - **Date**: 20 Dec 2021

## Tutorials

- [Automated REST API fuzzing using Schemathesis](https://killercoda.com/rafdev/scenario/rest-fuzzing-with-schemathesis) by **@RafDevX** and **@sofiaedv** at **KTH Royal Institute of Technology**

    - **Description**: Hands-on tutorial on fuzzing REST APIs with Schemathesis from the course **DD2482 Automated Software Testing and DevOps**

    - **Date**: 15 Oct 2023

## Articles

- [Introduction to Schemathesis: A Tool for Automatic Test Data Generation for Web APIs](https://gihyo.jp/article/2025/07/monthly-python-2507) (in Japanese) by **@ryu22e**

    - **Description**: A great introduction to Schemathesis 4 that covers both CLI usage and pytest integration with many practical examples.

    - **Date**: Jul 2025

- [Create a Cracker of an Open API Contract with VS Code, Spectral, Prism and Schemathesis](https://blog.hungovercoders.com/datagriff/2023/12/22/create-a-cracker-of-an-open-api-contract-with-vs-code-spectral-prism-and-schemathesis.html) by **@dataGriff**

    - **Description**: Detailed walkthrough of contract-first API design and testing workflow.

    - **Date**: 22 Dec 2023

- [Boost Your FastAPI Reliability with Schemathesis Automated Testing](https://medium.com/@jeremy3/boost-your-fastapi-reliability-with-schemathesis-automated-testing-e8b70ff704f6) by **@Jeremy**

    - **Description**: Implementation guide for Schemathesis testing in FastAPI projects.

    - **Date**: 17 Jul 2023

- [Implementing Schemathesis at PayLead](https://medium.com/paylead/implementing-schemathesis-at-paylead-a469a5d43626) by **Jérémy Pelletier** at **PayLead**

    - **Description**: Case study including custom hooks, stateful testing, and CI/CD integration.

    - **Date**: 29 May 2023

- [Auto-Generating & Validating OpenAPI Docs in Rust: A Streamlined Approach with Utoipa and Schemathesis](https://identeco.de/en/blog/generating_and_validating_openapi_docs_in_rust/) by **identeco**

    - **Description**: Integration of OpenAPI doc generation with Utoipa and validation with Schemathesis in Rust applications.

    - **Date**: 01 Jun 2023

- [Testing APIFlask with schemathesis](http://blog.pamelafox.org/2023/02/testing-apiflask-with-schemathesis.html) by **@pamelafox**

    - **Description**: Technical guide for testing APIFlask applications using Schemathesis.

    - **Date**: 27 Feb 2023

- [Using Hypothesis and Schemathesis to Test FastAPI](https://testdriven.io/blog/fastapi-hypothesis/) by **@amalshaji**

    - **Description**: Technical overview of property-based testing in FastAPI with Hypothesis and Schemathesis.

    - **Date**: 06 Sep 2022

- [How to use Schemathesis to test Flask API in GitHub Actions](https://notes.lina-is-here.com/2022/08/04/schemathesis-docker-compose.html) by **@lina-is-here**

    - **Description**: Implementation guide for Schemathesis with Flask API in GitHub Actions.

    - **Date**: 04 Aug 2022

- [Using API schemas for property-based testing](https://habr.com/ru/company/oleg-bunin/blog/576496/) (in Russian) by **@Stranger6667**

    - **Description**: Technical article covering Schemathesis for property-based API testing.

    - **Date**: 07 Sep 2021

- [Schemathesis: property-based testing for API schemas](https://dygalo.dev/blog/schemathesis-property-based-testing-for-api-schemas/) by **@Stranger6667**

    - **Description**: Introduction to the concepts behind Schemathesis and property-based testing for APIs.

    - **Date**: 26 Nov 2019

## Videos

- [Fuzzing REST APIs for Security Testing](https://youtu.be/ZdshB1qcgvw) by **Alina Kostetska** at **RoboCon 2024**

    - **Description**: Security-focused presentation demonstrating how to use Schemathesis / Robot Framework integration for fuzzing REST APIs to discover vulnerabilities.

    - **Date**: 04 Nov 2024

- [API Fuzzing: What it is and why you should use it](https://youtu.be/wX3GMJY9B6A) by **José Haro Peralta**

    - **Description**: Technical overview and demonstration of Schemathesis capabilities.

    - **Date**: 14 Feb 2023

- [Automated Testing with Schemathesis](https://appdev.consulting.redhat.com/tracks/contract-first/automated-testing-with-schemathesis.html) with [video tutorial](https://www.youtube.com/watch?v=4r7OC-lBKMg) by **Red Hat**

    - **Description**: Hands-on tutorial for implementing API testing with Schemathesis.

    - **Date**: 09 Feb 2023

- [Effective API schemas testing](https://youtu.be/VVLZ25JgjD4) from DevConf.cz by **@Stranger6667**

    - **Description**: Technical presentation on Schemathesis for property-based API schema testing.

    - **Date**: 24 Mar 2021

- [API-schema-based testing with schemathesis](https://www.youtube.com/watch?v=9FHRwrv-xuQ) from EuroPython 2020 by **@hultner**

    - **Description**: Technical introduction to property-based API testing with Schemathesis.

    - **Date**: 23 Jul 2020 
