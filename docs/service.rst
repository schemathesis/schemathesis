Schemathesis as a Service
=========================

Schemathesis as a Service on `Schemathesis.io <https://app.schemathesis.io/auth/sign-up/?utm_source=oss_docs&utm_content=saas_docs_top>`_ provides a hosted environment, extending your testing capabilities with additional features accessible in both free and premium plans.

.. note::

    For a step-by-step guide on getting started with Schemathesis.io, visit our `Quick Start Guide <https://docs.schemathesis.io/quick-start/>`_.

Schema Analysis (Experimental)
------------------------------

Schemathesis CLI now includes an experimental feature that integrates with Schemathesis.io to improve the efficiency and realism of generated test cases for better bug detection. This feature optimizes test case generation by:

- Inferring "format" keywords for appropriate string data generation
- Providing data generation strategies for uncommon media types (e.g., application/pdf)
- Inferring data generation strategies for GraphQL scalars
- Adjusting schemas for faster data generation in Schemathesis
- Detecting the web server to generate more targeted test data and avoid rejected inputs

By enabling this feature, you can potentially benefit from faster achievement of higher edge case coverage and tests that reach deeper into your API implementation.

To enable schema analysis, use the ``--experimental=schema-analysis`` CLI option. It is also automatically enabled when the ``--report`` CLI option is used.

.. note::
 
    During the experimental phase, schema analysis is available free of charge and does not require signup. By using this feature, you automatically agree to Schemathesis.io's `Terms of Service <https://schemathesis.io/legal/terms>`_ and `Privacy Policy`_.

Please note that this feature is still experimental, and its effectiveness may vary depending on the API being tested. We are actively refining the algorithms and techniques used. If you encounter any issues or have suggestions, please open an issue on our GitHub repository or reach out via our support channels.

Uploading Reports to Schemathesis.io
------------------------------------

When you use the Schemathesis CLI to run tests, you have the option to upload test reports to Schemathesis.io for a more detailed analysis and continuous tracking over time.
This can be done by using the ``--report`` flag with your CLI commands.

To store a report for later upload, you can first save it using the ``--report=report.tar.gz`` CLI option. Afterward, you can upload it with the ``st upload report.tar.gz`` command.

What Data is Sent?
------------------

When you choose to upload your test reports, the following data is included in the reports sent to Schemathesis.io by the CLI:

- **Metadata**:

  - Information about your host machine to help us understand our users better.
  - Collected data includes your Python interpreter version, implementation, system/OS name, the used Docker image name (if any) and release.

- **Test Runs**:

  - Most of the Schemathesis runner's events are included, encompassing all generated data and explicitly passed headers.
  - Sensitive data within the generated test cases and received responses is automatically sanitized by default, replaced with the string ``[Filtered]`` to prevent accidental exposure.
  - Further information on what is considered sensitive and how it is sanitized can be found at :ref:`Sanitizing Output <sanitizing-output>`.

- **Environment Variables**:

  - Some environment variables specific to CI providers are included.
  - These are used to comment on pull requests.

- **Command-Line Options**:

  - Command-line options without free-form values are sent to help us understand how you use the CLI.
  - Rest assured, any sensitive data passed through command-line options is sanitized by default.

For more details on our data handling practices, please refer to our `Privacy Policy <https://schemathesis.io/legal/privacy>`_. If you have further questions or concerns about data handling, feel free to contact us at `support@schemathesis.io <mailto:support@schemathesis.io>`_.

For information on data access, retention, and deletion, please refer to the `FAQ section <https://docs.schemathesis.io/faq>`_ in our SaaS documentation.
