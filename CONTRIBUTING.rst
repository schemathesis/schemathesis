Contributing to Schemathesis
============================

Welcome! Thank you for considering contributing to Schemathesis. Your feedback and contributions are invaluable to us!

.. contents::
   :depth: 2
   :backlinks: none

Prerequisites for Code Contributions
------------------------------------

**For code contributions**: Make sure you have the following installed:

- Python 3.8 or higher
- ``pre-commit``
- ``tox``

.. code:: bash

    python -m pip install pre-commit tox

**For documentation contributions**: No specific prerequisites are required.

Feature Requests and Feedback
-----------------------------

If you'd like to suggest a feature or provide feedback, feel free to `submit an issue <https://github.com/schemathesis/schemathesis/issues>`_. When submitting your issue, it helps to provide:

- **Title**: Write a simple and descriptive title to identify your suggestion.
- **Details**: Provide as many details as possible. Explain your context and how you envision the feature working.
- **Usefulness**: Explain why this feature or improvement would be beneficial.
- **Scope**: Keep the scope of the feature narrow to make it easier to implement. For example, focus on a specific use-case rather than a broad feature set.

Reporting Bugs
--------------

If you encounter a bug, please report it in the `issue tracker <https://github.com/schemathesis/schemathesis/issues>`_. When filing a bug report, please include:

- **Title**: Write a simple and descriptive title to identify the problem.
- **Reproduction Steps**: Describe the exact steps to reproduce the problem in as much detail as possible.
- **Observed Behavior**: Describe the behavior you observed and what makes it a problem.
- **Expected Behavior**: Explain which behavior you expected to see instead and why.
- **Versions**: Include Python and Schemathesis versions. Also, confirm if the issue persists in the latest version of Schemathesis.
- **Additional Context**: Logs, error messages, or screenshots are often very helpful.

**What happens next?**: After you submit an issue, we aim to review and respond as soon as possible.
If you don't receive a response within a few days, feel free to add a new comment to the thread to bring it to our attention again.

Submitting Pull Requests
------------------------

We welcome contributions to the codebase! If you'd like to submit a pull request (PR), please follow these steps:

1. **Fork the Repository**: Fork the Schemathesis repository on GitHub.
2. **Install Development Tools**: Install the development dependencies using the following command:

.. code:: bash

    python -m pip install -e ".[dev]"

This will install all the necessary packages for development, including those for documentation and tests.

3. **Set Up Pre-commit Hooks**: Enable `pre-commit <https://pre-commit.com>`_.

.. code:: bash

    pre-commit install

4. **Branching**: Create a new branch and switch to it. Target your pull request to the ``master`` branch of the main repository.
5. **Coding Standards**: Follow `PEP-8 <https://pep8.org/>`_ for naming conventions and use `ruff <https://github.com/astral-sh/ruff>`_ for code formatting.
6. **Write Tests**: Preferably, write integration tests that run the whole Schemathesis CLI.
7. **Run Tests**:

.. code:: bash

    tox -e py311

8. **Update Changelog**: Add a corresponding entry to ``changelog.rst`` located in the ``docs`` directory.
9. **Commit Your Changes**: Use the `Conventional Commits <https://www.conventionalcommits.org/en/>`_ format. For example, features could be ``feat: add new validation feature`` and bug fixes could be ``fix: resolve issue with validation``.

**What happens next?**: After submitting, your pull request will be reviewed.
If you don't hear back within a few days, feel free to add a comment to the pull request to draw our attention.

Contributing to Documentation
-----------------------------

We recommend installing Schemathesis with the "docs" extra for all the dependencies needed for documentation:

.. code:: bash

    python -m pip install -e ".[docs]"

To preview your changes:

.. code:: bash

    cd docs/
    make html
    python -m http.server -d _build/html/

Then open ``http://0.0.0.0:8000/`` in your browser.

Community and Support
---------------------

For more informal discussions or questions, join us on `Discord <https://discord.gg/R9ASRAmHnA>`_.

Maintainers
-----------

At present, the core developers are:

- Dmitry Dygalo (`@Stranger6667`_)

Preferred communication language
--------------------------------

We prefer to keep all communications in English.

Thanks!

.. _@Stranger6667: https://github.com/Stranger6667
