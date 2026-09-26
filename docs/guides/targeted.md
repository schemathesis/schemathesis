# Targeted Testing

This guide shows how to steer data generation toward inputs that maximize a metric, such as response time, so Schemathesis reaches slow or failing inputs in fewer test cases.

## Prerequisites

- Schemathesis installed or available via `uvx`
- An API with an OpenAPI schema

## Which phases use targeting

Targeting guides Hypothesis-based generation, so it applies in the **fuzzing** and **stateful** phases. The examples and coverage phases use fixed inputs; metrics are computed there but do not change what gets sent. See [Testing Phases](../explanations/data-generation.md#testing-phases) for what each phase does.

## Maximize response time from the CLI

Pass the metric name to `--generation-maximize`:

```console
$ uvx schemathesis run http://127.0.0.1:8000/openapi.json --generation-maximize response_time
```

The built-in metric is `response_time`. In `schemathesis.toml`:

```toml
[generation]
maximize = "response_time"
```

Consider an endpoint that slows down by 10 ms for every `0` in the request body and fails with more than ten zeros:

```python
import asyncio
import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

SCHEMA = {
    "openapi": "3.0.0",
    "info": {"title": "Demo", "version": "1.0"},
    "paths": {
        "/performance": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"type": "array", "items": {"type": "integer"}}}},
                },
                "responses": {
                    "200": {"description": "OK"},
                    "422": {"description": "Invalid input"},
                },
            }
        }
    },
}


async def performance(request: Request) -> JSONResponse:
    try:
        numbers = json.loads(await request.body())
    except ValueError:
        numbers = None
    if not isinstance(numbers, list) or not all(type(item) is int for item in numbers):
        return JSONResponse({"detail": "Expected an array of integers"}, status_code=422)
    zeros = str(numbers).count("0")
    # Each zero adds 10 ms to the response time
    await asyncio.sleep(0.01 * zeros)
    if zeros > 10:
        return JSONResponse({"detail": "Internal error"}, status_code=500)
    return JSONResponse({"result": "success"})


async def openapi(request: Request) -> JSONResponse:
    return JSONResponse(SCHEMA)


app = Starlette(
    routes=[
        Route("/performance", performance, methods=["POST"]),
        Route("/openapi.json", openapi),
    ]
)
```

Save it as `app.py`, start it with `uvicorn app:app --port 8000`, and run the command above. Inputs that take longer are favored, so the fuzzing phase moves toward bodies with more zeros:

```
Test Phases:
  ⏭  Examples
  ✅ Coverage
  ❌ Fuzzing
  ⏭  Stateful (not applicable)

Failures:
  ❌ Server error: 1
  ❌ Undocumented HTTP status code: 1
```

Results vary between runs because generation is random. Compare runs with and without `--generation-maximize` on your own API, using the same `--max-examples`, to see whether targeting helps there.

## Register a custom metric

A metric is a function that takes a `MetricContext` (with the `case` and its `response`) and returns a `float`:

```python
# metrics.py
import schemathesis


@schemathesis.metric
def response_size(ctx: schemathesis.MetricContext) -> float:
    return float(len(ctx.response.content))
```

Load the module through `SCHEMATHESIS_HOOKS` and pass the function name to `--generation-maximize`:

```console
$ export SCHEMATHESIS_HOOKS=metrics
$ uvx schemathesis run http://127.0.0.1:8000/openapi.json --generation-maximize response_size
```

## Target a metric in pytest

The pytest integration does not apply `--generation-maximize` or metrics registered with `@schemathesis.metric`. Compute the value from the response yourself and pass it to [`hypothesis.target`](https://hypothesis.readthedocs.io/en/latest/reference/api.html#hypothesis.target):

```python
# test_api.py
import hypothesis
import schemathesis

from app import app

schema = schemathesis.openapi.from_asgi("/openapi.json", app)


@schema.parametrize()
def test_api(case):
    response = case.call()
    hypothesis.target(response.elapsed, label="response_time")
    case.validate_response(response)
```

`response.elapsed` is the response time in seconds. Run `pytest test_api.py`; the test fails with the server error once a body with more than ten zeros is generated:

```
  | schemathesis.core.failures.FailureGroup: Schemathesis found 2 distinct failures
  | - Server error
...
FAILED test_api.py::test_api[POST /performance] - ...
```

`hypothesis.target` only affects generated test cases; in explicit examples (the examples and coverage phases under pytest) it has no effect.

## Troubleshooting

**`--generation-maximize` rejects the metric name.** The name must match a registered metric. For custom metrics, check that `SCHEMATHESIS_HOOKS` points to the module that defines it and that the module imports without errors.

**No difference compared to an untargeted run.** Targeting needs several generated test cases per operation to have an effect. Raise `--max-examples`, and check that the operation reaches the fuzzing phase.
