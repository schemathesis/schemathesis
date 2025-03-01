Command Line Interface
======================

Installing Schemathesis installs the ``schemathesis`` script to your virtualenv, which you can use to test your APIs

.. note::

    To see the full list of CLI options & commands use the ``--help`` option.

.. _extend-cli:

Extending CLI
-------------

To fit Schemathesis to your workflows, you might want to extend it with your custom checks or setup environment before the test run.

Extensions should be placed in a separate Python module. 
Then, Schemathesis should be informed about this module via the ``SCHEMATHESIS_HOOKS`` environment variable:

.. code:: bash

    export SCHEMATHESIS_HOOKS=myproject.tests.hooks
    st run http://127.0.0.1/openapi.yaml

Also, depending on your setup, you might need to run this command with a custom ``PYTHONPATH`` environment variable like this:

.. code:: bash

    export PYTHONPATH=$(pwd)
    export SCHEMATHESIS_HOOKS=myproject.tests.hooks
    st run https://example.com/api/swagger.json

The passed value will be treated as an importable Python path and imported before the test run.

.. note::

    You can find more details on how to extend Schemathesis in the :ref:`Extending Schemathesis <enabling-extensions>` section.

Registering custom checks
~~~~~~~~~~~~~~~~~~~~~~~~~

To use your custom checks with Schemathesis CLI, you need to register them via the ``schemathesis.check`` decorator:

.. code:: python

    import schemathesis


    @schemathesis.check
    def new_check(ctx, response, case):
        # some awesome assertions!
        pass

The registered check should accept ``ctx``, a ``response`` with ``schemathesis.Response`` type and
``case`` with ``schemathesis.Case`` type. This code should be placed in the module you pass to the ``SCHEMATHESIS_HOOKS`` environment variable.

Then your checks will be available in Schemathesis CLI, and you can use them via the ``-c`` command-line option.

.. code:: bash

    $ SCHEMATHESIS_HOOKS=module.with.checks
    $ st run -c new_check https://example.com/api/swagger.json

Additionally, checks may return ``True`` to skip the check under certain conditions. For example, you may only want to run checks when the
response code is ``200``.

.. code:: python

    import schemathesis


    @schemathesis.check
    def conditional_check(ctx, response, case):
        if response.status_code == 200:
            ...  # some awesome assertions!
        else:
            # check not relevant to this response, skip test
            return True

Skipped check calls will not be reported in the run summary.

.. note::

    Learn more about writing custom checks :ref:`here <writing-custom-checks>`.
