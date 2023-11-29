Welcome to Schemathesis!
========================

Schemathesis is a tool that automates API testing by leveraging API specs as a blueprints for generating test cases.
It focuses on testing for general properties — such as ensuring no input leads to server errors and all responses adhere to the API spec — rather than just checking specific input-output combinations.

This approach broadens your testing suite's capability to detect a wide range of potential issues, from trivial bugs to critical vulnerabilities.

Built on top of the widely-used `Hypothesis <http://hypothesis.works/>`_ framework for property-based testing, it offers the following advantages:

- 🕒 **Time-Saving**: Automatically generates test cases, freeing you from manual test writing.
- 🔍 **Comprehensive**: Utilizes fuzzing techniques for both common and edge-case scenarios.
- 🛠️ **Flexible**: Supports OpenAPI and GraphQL. Operates even with partially complete schemas.
- 🎛️ **Customizable**: Extend almost any aspect of the testing process through Python.
- 🔄 **Reproducible**: Generates code samples for quick replication of any failing test cases.

Here’s a simplified overview of how Schemathesis works:

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

Community & Support
-------------------

Join our `Discord <https://discord.gg/R9ASRAmHnA>`_ channel for access to learning resources and prompt support to resolve questions and expand your knowledge.

If you're a large enterprise or startup seeking specialized assistance, we offer commercial support to help you integrate Schemathesis effectively into your workflows. This includes:

- Quicker response time for your queries.
- Direct consultation to work closely with your API specification, optimizing the Schemathesis setup for your specific needs.

To discuss a custom support arrangement that best suits your organization, please contact our support team at `support@schemathesis.io <mailto:support@schemathesis.io>`_.

User's Guide
------------

The User Guide explains different parts of Schemathesis and how they can be used, customized, and extended.
It generally follows the operational workflow of Schemathesis, detailing steps from initial schema loading to test generation, execution, and result evaluation.

.. toctree::
   :maxdepth: 2

   getting-started
   data-generation
   cli
   python
   continuous_integration
   experimental
   service
   auth
   contrib
   stateful
   sanitizing
   compatibility
   examples
   graphql
   targeted
   extending
   recipes
   additional-content
   api
   faq
   changelog
