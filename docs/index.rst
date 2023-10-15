Schemathesis: catch crashes, validate specs, and save time
==========================================================

Schemathesis focuses on automating your API testing to catch crashes and spec violations.
Built on top of the widely-used `Hypothesis <http://hypothesis.works/>`_ framework for property-based testing, it offers the following advantages:

- 🕒 **Time-Saving**: Automatically generates test cases, freeing you from manual test writing.
- 🔍 **Comprehensive**: Utilizes fuzzing techniques for both common and edge-case scenarios.
- 🛠️ **Flexible**: Supports OpenAPI and GraphQL. Operates even with partially complete schemas.
- 🎛️ **Customizable**: Extend almost any aspect of the testing process through Python.
- 🔄 **Reproducible**: Generates code samples for quick replication of any failing test cases.

.. contents:: Table of Contents
   :depth: 2

.. note::

   Join us on `Discord <https://discord.gg/R9ASRAmHnA>`_ for real-time support.

Getting Started
===============

Choose from multiple ways to start testing your API with Schemathesis.

.. note:: Your API schema can be either a URL or a local path to a JSON/YAML file.

Command-Line Interface
----------------------

Quick and easy for those who prefer the command line.

Python
^^^^^^

1. Install via pip: ``python -m pip install schemathesis``
2. Run tests:

.. code-block:: bash

   st run --checks all https://example.schemathesis.io/openapi.json

Docker
^^^^^^

1. Pull Docker image: ``docker pull schemathesis/schemathesis:stable``
2. Run tests:

.. code-block:: bash

   docker run schemathesis/schemathesis:stable
      run --checks all https://example.schemathesis.io/openapi.json

Python Library
--------------

For more control and customization, integrate Schemathesis into your Python codebase.

1. Install via pip: ``python -m pip install schemathesis``
2. Add to your tests:

.. code-block:: python

   import schemathesis

   schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")


   @schema.parametrize()
   def test_api(case):
       case.call_and_validate()

.. note:: See a complete working example project in the `/example <https://github.com/schemathesis/schemathesis/tree/master/example>`_ directory.

GitHub Integration
------------------

GitHub Actions
^^^^^^^^^^^^^^

Run Schemathesis tests as a part of your CI/CD pipeline. Add this YAML configuration to your GitHub Actions:

.. code-block:: yaml

   api-tests:
     runs-on: ubuntu-20.04
     steps:
       - uses: schemathesis/action@v1
         with:
           schema: "https://example.schemathesis.io/openapi.json"
           # OPTIONAL. Add Schemathesis.io token for pull request reports
           token: ${{ secrets.SCHEMATHESIS_TOKEN }}

For more details, check out our `GitHub Action <https://github.com/schemathesis/action>`_ repository.

GitHub App
^^^^^^^^^^

Receive automatic comments in your pull requests and updates on GitHub checks status. Requires usage of our `SaaS platform <https://app.schemathesis.io/auth/sign-up/?utm_source=oss_docs&utm_content=index_note>`_.

1. Install the `GitHub app <https://github.com/apps/schemathesis>`_.
2. Enable in your repository settings.

Software as a Service
---------------------

If you prefer an all-in-one solution with quick setup, we have a `free tier <https://schemathesis.io/#pricing>`_ available.

How it Works
============

Here’s a simplified overview of how Schemathesis operates:

1. **Test Generation**: Using the API schema to create a test generator that you can fine-tune to your testing requirements.
2. **Execution and Adaptation**: Sending tests to the API and adapting through statistical models and heuristics to optimize subsequent cases based on responses.
3. **Analysis and Minimization**: Checking responses to identify issues. Minimizing means simplifying failing test cases for easier debugging.
4. **Stateful Testing**: Running multistep tests to assess API operations in both isolated and integrated scenarios.
5. **Reporting**: Generating detailed reports with insights and cURL commands for easy issue reproduction.

Research Findings on Open-Source API Testing Tools
--------------------------------------------------

Our study, presented at the **44th International Conference on Software Engineering**, highlighted Schemathesis's performance:

- **Defect Detection**: identified a total of **755 bugs** in **16 services**, finding between **1.4× to 4.5× more defects** than the second-best tool in each case.
- **High Reliability**: consistently operates seamlessly on any project, ensuring unwavering stability and reliability.

Explore the full paper at `IEEEXplore <https://ieeexplore.ieee.org/document/9793781>`_ or pre-print at `arXiv <https://arxiv.org/abs/2112.10328>`_.

Commercial Support
==================

If you're a large enterprise or startup seeking specialized assistance, we offer commercial support to help you integrate Schemathesis effectively into your workflows. This includes:

- Quicker response time for your queries.
- Direct consultation to work closely with your API specification, optimizing the Schemathesis setup for your specific needs.

To discuss a custom support arrangement that best suits your organization, please contact our support team at `support@schemathesis.io <mailto:support@schemathesis.io>`_.

Additional Content
==================

Papers
------

- `Deriving Semantics-Aware Fuzzers from Web API Schemas <https://ieeexplore.ieee.org/document/9793781>`_ by **@Zac-HD** and **@Stranger6667**

  - **Description**: Explores the automation of API testing through semantics-aware fuzzing. Presented at ICSE 2022.

  - **Date**: 20 Dec 2021

Articles
--------

- `Auto-Generating & Validating OpenAPI Docs in Rust: A Streamlined Approach with Utoipa and Schemathesis <https://identeco.de/en/blog/generating_and_validating_openapi_docs_in_rust/>`_ by **identeco**

  - **Description**: Demonstrates OpenAPI doc generation with Utoipa and validating it with Schemathesis.

  - **Date**: 01 Jun 2023
- `Testing APIFlask with schemathesis <http://blog.pamelafox.org/2023/02/testing-apiflask-with-schemathesis.html>`_ by **@pamelafox**

  - **Description**: Explains how to test APIFlask applications using Schemathesis.

  - **Date**: 27 Feb 2023
- `Using Hypothesis and Schemathesis to Test FastAPI <https://testdriven.io/blog/fastapi-hypothesis/>`_ by **@amalshaji**

  - **Description**: Discusses property-based testing in FastAPI with Hypothesis and Schemathesis.

  - **Date**: 06 Sep 2022
- `How to use Schemathesis to test Flask API in GitHub Actions <https://notes.lina-is-here.com/2022/08/04/schemathesis-docker-compose.html>`_ by **@lina-is-here**

  - **Description**: Guides you through setting up Schemathesis with Flask API in GitHub Actions.

  - **Date**: 04 Aug 2022
- `Using API schemas for property-based testing (RUS) <https://habr.com/ru/company/oleg-bunin/blog/576496/>`_ about Schemathesis by **@Stranger6667**

  - **Description**: Covers the usage of Schemathesis for property-based API testing.

  - **Date**: 07 Sep 2021
- `Schemathesis: property-based testing for API schemas <https://dygalo.dev/blog/schemathesis-property-based-testing-for-api-schemas/>`_ by **@Stranger6667**

  - **Description**: Introduces property-based testing for OpenAPI schemas using Schemathesis.

  - **Date**: 26 Nov 2019

Videos
------

- `Schemathesis tutorial <https://appdev.consulting.redhat.com/tracks/contract-first/automated-testing-with-schemathesis.html>`_ with an accompanying `video <https://www.youtube.com/watch?v=4r7OC-lBKMg>`_ by **Red Hat**

  - **Description**: Provides a hands-on tutorial for API testing with Schemathesis.

  - **Date**: 09 Feb 2023
- `Effective API schemas testing <https://youtu.be/VVLZ25JgjD4>`_ from DevConf.cz by **@Stranger6667**

  - **Description**: Talks about using Schemathesis for property-based API schema testing.

  - **Date**: 24 Mar 2021
- `API-schema-based testing with schemathesis <https://www.youtube.com/watch?v=9FHRwrv-xuQ>`_ from EuroPython 2020 by **@hultner**

  - **Description**: Introduces property-based API testing with Schemathesis.

  - **Date**: 23 Jul 2020

User's Guide
============

.. toctree::
   :maxdepth: 2

   introduction
   cli
   python
   continuous_integration
   experimental
   service
   auth
   contrib
   stateful
   how
   sensitive_output
   compatibility
   examples
   graphql
   targeted
   extending

Additional notes
================

.. toctree::
   :maxdepth: 2

   recipes
   api
   faq
   changelog
