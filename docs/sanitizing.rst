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

    schema = schemathesis.from_dict({...}, sanitize_output=False)

Disabling this option will turn off the automatic sanitization of sensitive data in the output.

For more advanced customization of the sanitization process, you can define your own sanitization configuration and pass it to the ``configure`` function.
Here's how you could do it:

.. code-block:: python

    import schemathesis

    # Create a custom config
    custom_config = (
        schemathesis.sanitization.Config(replacement="[Custom]")
        .with_keys_to_sanitize("X-Customer-ID")
        .with_sensitive_markers("address")
    )

    # Configure Schemathesis to use your custom sanitization configuration
    schemathesis.sanitization.configure(custom_config)

This will sanitize the ``X-Customer-ID`` headers (case-insensitive), and any fields containing the substring "address" (case-insensitive) in their names, with the string "[Custom]" in the generated test case and the received response.

This will sanitize the ``X-Customer-ID`` headers, and any fields containing the substring "address" in their names, with the string "[Custom]" in the generated test case and the received response.
