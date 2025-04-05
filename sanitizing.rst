.. _sanitizing-output:

Sanitizing Output
=================

Schemathesis automatically sanitizes sensitive data in both the generated test case and the received response to prevent accidental exposure of sensitive information.
This feature replaces certain headers, cookies, and other fields that could contain sensitive data with the string ``[Filtered]``.

.. note::
   Schemathesis does not sanitize sensitive data in response bodies due to the challenge of preserving the original formatting of the payload.

You can control this feature through the ``--sanitize-output`` CLI option:

.. code-block:: bash

   schemathesis run --sanitize-output=false ...

Or in Python tests:

.. code-block:: python

    from schemathesis import OutputConfig

    schema = schemathesis.openapi.from_dict({...}).configure(
        output=OutputConfig(sanitize=False)
    )

Disabling this option will turn off the automatic sanitization of sensitive data in the output.

For more advanced customization of the sanitization process, you can define your own sanitization configuration and pass it to the ``configure`` function.
Here's how you could do it:

.. code-block:: python

    import schemathesis

    # Replace configuration
    schemathesis.sanitization.configure(
        replacement="[Custom]",
        keys_to_sanitize=["X-Customer-ID"],
        sensitive_markers=["address"]
    )

    # Extend existing configuration
    schemathesis.sanitization.extend(
        keys_to_sanitize=["Additional-Key"],
        sensitive_markers=["password"]
    )

This will sanitize the ``X-Customer-ID`` headers, and any fields containing the substring "address" in their names, with the string "[Custom]" in the generated test case and the received response.
