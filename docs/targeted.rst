Targeted property-based testing
===============================

Schemathesis supports targeted property-based testing via utilizing ``hypothesis.target`` inside its runner and provides
an API to guide data generation towards certain pre-defined goals:

- ``response_time``. Hypothesis will try to generate input that will more likely to have higher response time;

To illustrate this feature, consider the following AioHTTP request handler, that contains a hidden performance problem -
the more zeroes are in the input number, the slower it works, and if there are more than 10 zeroes, it will cause an internal
server error:

.. code:: python

    import asyncio
    from aiohttp import web


    async def performance(request: web.Request) -> web.Response:
        decoded = await request.json()
        number = str(decoded).count("0")
        if number > 0:
            # emulate hard work
            await asyncio.sleep(0.01 * number)
        if number > 10:
            raise web.HTTPInternalServerError
        return web.json_response({"slow": True})

Let's take a look if Schemathesis can discover this issue and how much time it will take:

.. code:: bash

    $  st run --hypothesis-max-examples=100000 http://127.0.0.1:8081/schema.yaml
    ...
    1. Received a response with 5xx status code: 500

    Check           : not_a_server_error
    Body            : 58150920460703009030426716484679203200

    Run this Python code to reproduce this failure:

        requests.post('http://127.0.0.1:8081/api/performance', json=58150920460703009030426716484679203200)

    Or add this option to your command line parameters: --hypothesis-seed=240368931405400688094965957483327791742
    ================================================== SUMMARY ==================================================

    Performed checks:
        not_a_server_error                    67993 / 68041 passed          FAILED

    ============================================ 1 failed in 662.16s ===========================================

And with targeted testing (``.hypothesis`` directory was removed between these test runs to avoid reusing results):

.. code:: bash

    $  st run --target=response_time --hypothesis-max-examples=100000 http://127.0.0.1:8081/schema.yaml
    ...
    1. Received a response with 5xx status code: 500

    Check           : not_a_server_error
    Body            : 2600050604444474172950385505254500000

    Run this Python code to reproduce this failure:

        requests.post('http://127.0.0.1:8081/api/performance', json=2600050604444474172950385505254500000)

    Or add this option to your command line parameters: --hypothesis-seed=340229547842147149729957578683815058325
    ================================================== SUMMARY ==================================================

    Performed checks:
        not_a_server_error                    22039 / 22254 passed          FAILED

    ============================================ 1 failed in 305.50s ===========================================

This behavior is reproducible in general, but not guaranteed due to the randomness of data generation. However, it shows
a significant testing time reduction, especially on a large number of examples.

Hypothesis `documentation <https://hypothesis.readthedocs.io/en/latest/details.html#targeted-example-generation>`_ provides a detailed explanation of what targeted property-based testing is.

Custom targets
~~~~~~~~~~~~~~

You can register your own targets with the ``schemathesis.target`` decorator. The function should accept ``schemathesis.targets.TargetContext`` and return a float value:

.. code:: python

    import schemathesis


    @schemathesis.target
    def new_target(context) -> float:
        return float(len(context.response.content))

Such a function will cause Hypothesis to generate input that is more likely to produce larger responses.
