.. _sensitive-output:

Masking Sensitive Output
========================

Schemathesis automatically masks sensitive data in both the generated test case and the received response to prevent accidental exposure of sensitive information.
This feature replaces certain headers, cookies, and other fields that could contain sensitive data with the string ``[Masked]``.

.. note::
   Schemathesis does not mask sensitive data in response bodies due to its complexity. You can customize the masking process to handle response bodies or other specific cases.

You can control this feature through the ``--mask-sensitive-output`` CLI option:

.. code-block:: bash

   schemathesis run --mask-sensitive-output=false ...

Disabling this option will turn off the automatic masking of sensitive data in the output.

For more advanced customization of the masking process, you can define a ``mask_sensitive_output`` hook.
See :ref:`Customizing Sensitive Output Masking <sensitive-output-hook>` for more details.
