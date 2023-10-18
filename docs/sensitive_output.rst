.. _sensitive-output:

Masking Sensitive Output
========================

Schemathesis automatically masks sensitive data in both the generated test case and the received response to prevent accidental exposure of sensitive information.
This feature replaces certain headers, cookies, and other fields that could contain sensitive data with the string ``[Masked]``.

.. note::
   Schemathesis does not mask sensitive data in response bodies due to its complexity.

You can control this feature through the ``--mask-sensitive-output`` CLI option:

.. code-block:: bash

   schemathesis run --mask-sensitive-output=false ...

Or in Python tests:

.. code-block:: python

    schema = schemathesis.from_dict({...}, mask_sensitive_output=False)

Disabling this option will turn off the automatic masking of sensitive data in the output.

For more advanced customization of the masking process, you can define your own masking configuration and pass it to the ``configure`` function.
Here's how you could do it:

.. code-block:: python

    import schemathesis

    # Create a custom config
    custom_config = (
        schemathesis.masking.Config(replacement="[Custom]")
        .with_keys_to_mask("X-Customer-ID")
        .with_sensitive_markers("address")
    )

    # Configure Schemathesis to use your custom masking configuration
    schemathesis.masking.configure(custom_config)

This will mask the ``X-Customer-ID`` headers, and any fields containing the substring "address" in their names, with the string "[Custom Masked]" in the generated test case and the received response.
