Experimental features
=====================

Introduction
------------

This section provides an overview of experimental features in Schemathesis - features that are under development and available for testing, but not yet considered stable.

This section provides an overview of experimental features in Schemathesis.
These are features that are under development, available for user testing but not yet stable.
Experimental features offer a glimpse into upcoming functionalities and enhancements, providing users an opportunity to try out and contribute feedback to shape their final form.

.. note::

   Experimental features can change or be removed in any minor version release.

Enabling Experimental Features
------------------------------

.. _experimental-cli:

In CLI
~~~~~~

To enable an experimental feature via the CLI, use the ``--experimental`` option.

For example, to enable experimental support for OpenAPI 3.1:

.. code-block:: bash

   st run https://example.schemathesis.io/openapi.json --experimental=openapi-3.1

.. _experimental-python:

In Python Tests
~~~~~~~~~~~~~~~

To enable experimental features globally across all your Schemathesis tests, you can use the ``enable`` method on the desired experimental feature.

Here's an example:

.. code-block:: python

    import schemathesis

    # Globally enable OpenAPI 3.1 experimental feature
    schemathesis.experimental.openapi_3_1.enable()

By doing this, all schemas loaded afterwards will automatically use the enabled experimental features.

.. note::

    Enabling an experimental feature globally will affect all your tests. Use this feature with caution.


Current Experimental Features
-----------------------------

.. _experimental-openapi-31:

Open API 3.1
~~~~~~~~~~~~

Provides partial support for OpenAPI 3.1. This includes compatible JSON Schema validation for API responses.
Note that data generation is still compatible only with OpenAPI 3.0.

Enabling this feature also automatically enables UUID format support.

.. _openapi-31-cli:

In CLI
~~~~~~

.. code-block:: bash

   st run https://example.schemathesis.io/openapi.json --experimental=openapi-3.1

.. _openapi-31-python:

In Python Tests
~~~~~~~~~~~~~~~

.. code-block:: python

    import schemathesis

    # Globally enable OpenAPI 3.1 experimental feature
    schemathesis.experimental.openapi_3_1.enable()

For more details, join the `GitHub Discussion <https://github.com/schemathesis/schemathesis/discussions/1822>`_.

Stabilization of Experimental Features
--------------------------------------

Criteria for moving a feature from experimental to stable status include:

- Full coverage of planned functionality
- API design stability, assessed through user feedback and internal review

Providing Feedback
------------------

Feedback is crucial for the development and stabilization of experimental features. We encourage you to share your thoughts via `GitHub Discussions <https://github.com/schemathesis/schemathesis/discussions>`_

.. note::

   When you use an experimental feature, a notice will appear in your test output, providing a link to the corresponding GitHub discussion where you can leave feedback.
