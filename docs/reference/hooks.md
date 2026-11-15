# Hooks Reference

## Schema-Level Hooks

Hooks that execute during schema loading and operation initialization.

| Hook | Signature | Execution Order | Modifies |
|------|-----------|-----------------|----------|
| **before_load_schema** | `(ctx, raw_schema: dict) -> None` | Before schema parsing | `raw_schema` dict |
| **after_load_schema** | `(ctx, schema: BaseSchema) -> None` | After schema instance created | `schema` object |
| **before_process_path** | `(ctx, path: str, methods: dict) -> None` | For each API path | `methods` dict |
| **before_init_operation** | `(ctx, operation: APIOperation) -> None` | For each operation | `APIOperation` object |
| **before_add_examples** | `(ctx, examples: list[Case]) -> None` | Before scheduling all existing examples for testing | `Case` list |

!!! note

    All schema-level hooks return `None` and modify their arguments in-place.

## Run-Lifecycle Hooks

Hooks that run once per `st run` or `st fuzz` invocation, and once per schema in a pytest session.

| Hook | Signature | Execution Stage | Purpose |
|------|-----------|-----------------|---------|
| **setup** | `(ctx, schema: BaseSchema) -> None` | After API probes, sign-up and dictionary loading, before the first test case | Seed test data, start services |
| **teardown** | `(ctx, schema: BaseSchema) -> None` | After testing ends, including failed or interrupted runs | Remove test data, stop services |

```python
import requests
import schemathesis


@schemathesis.hook
def setup(ctx, schema):
    requests.post(f"{schema.get_base_url()}/test-data/seed")


@schemathesis.hook
def teardown(ctx, schema):
    requests.post(f"{schema.get_base_url()}/test-data/reset")
```

An error in a `setup` hook is reported, fails the run, and testing still proceeds. Every `teardown` hook runs even when another one fails.

Under pytest, both hooks run as a fixture around all tests of a schema: `setup` before the first test, `teardown` after the last one and before the session fixtures those tests use are torn down. A schema from `schemathesis.pytest.from_fixture` gets them each time its fixture is created. An error in `setup` makes the tests of that schema error without running. With `pytest-xdist`, each worker runs both hooks.

## Data Generation Hooks

Control how test data is generated for each request component.

| Hook Pattern | Signature | Purpose | Return |
|--------------|-----------|---------|---------|
| **before_generate_{component}** | `(ctx, strategy: SearchStrategy) -> SearchStrategy` | Modify generation strategy | Modified strategy |
| **filter_{component}** | `(ctx, value: Any) -> bool` | Exclude generated values | `True` to keep, `False` to reject |
| **map_{component}** | `(ctx, value: Any) -> Any` | Transform generated values | Modified value |
| **flatmap_{component}** | `(ctx, value: Any) -> SearchStrategy` | Generate dependent values | New strategy based on input |

Where `{component}` is one of: `path_parameters`, `headers`, `cookies`, `query`, `body`, `case`

## Request-Lifecycle Hooks

Hooks that execute during test case execution.

| Hook | Signature | Execution Stage | Purpose |
|------|-----------|-----------------|---------|
| **before_call** | `(ctx, case: Case, kwargs) -> None` | Before HTTP request | Modify test case (headers, body, etc.) |
| **after_call** | `(ctx, case: Case, response: Response) -> None` | After HTTP response | Inspect/modify response before checks |
| **after_network_error** | `(ctx, case: Case, request: PreparedRequest) -> None` | When HTTP request fails at network level | Record or log failed connection attempts |
| **after_validate** | `(ctx, case: Case, response: Response, results: list[CheckResult]) -> None` | After all checks run | Observe check outcomes for logging or reporting |
| **filter_failure** | `(ctx, failure: Failure, case: Case, response: Response) -> bool` | Once per check failure | Drop failures that are not your API's fault |

**Success flow:** `before_call` -> HTTP Request -> `after_call` -> Checks -> `filter_failure` -> `after_validate`

Return `False` from `filter_failure` to drop a failure. A dropped failure does not fail the run, does not spend the `--max-failures` budget, does not reach deduplication, and writes no crash file; the run reports how many were dropped:

```python
@schemathesis.hook
def filter_failure(ctx, failure, case, response):
    # A flaky upstream is not this API's bug
    return response.status_code != 503
```

```
Filtered: 3 (dropped by `filter_failure`)
```

Use it for failures that depend on the response rather than on which operation was hit. For a fixed set of failures you have accepted and want reviewed, use a [baseline](../guides/baseline.md) instead. Network errors and failures raised from a check's `after_run` never reach this hook - they have no response to judge.

**Network error flow:** `before_call` -> HTTP Request -> `after_network_error` -> (re-raises)

`CheckResult` fields: `name` (check name), `status` (`Status.SUCCESS` or `Status.FAILURE`), `failure` (`Failure` instance or `None`).

## Hook Registration

| Scope | Registration Method | Available Hooks |
|-------|-------------------|-----------------|
| **Global** | `@schemathesis.hook` or `@schemathesis.hook("hook_name")` | All hooks |
| **Schema** | `@schema.hooks.hook` or `@schema.hooks.hook("hook_name")` | All hooks except `before_load_schema`, `after_load_schema`, `before_call` |
| **Test** | `@schema.hooks.apply(hook_func)` | All hooks except `before_load_schema`, `after_load_schema`, `before_call`, `after_call`, `after_network_error`, `setup`, `teardown` |

**Hook Name Detection:** If not specified, uses function name (must match hook name exactly).

## Conditional Application

Apply hooks selectively using filters.

### Filter Methods

| Method | Logic | Description |
|--------|-------|-------------|
| `.apply_to(...)` | AND | Apply only when ALL conditions match |
| `.skip_for(...)` | OR | Skip when ANY condition matches |

### Available Filters

| Filter | Type | Example | Description |
|--------|------|---------|-------------|
| `path` | `str | list[str]` | `"/users"` | Exact path match |
| `path_regex` | `str` | `r"^/users/\d+$"` | Path pattern match |
| `method` | `str | list[str]` | `["GET", "POST"]` | HTTP method |
| `method_regex` | `str` | `"GET|POST"` | HTTP method pattern match |
| `name` | `str | list[str]` | `"GET /users"` | Operation name |
| `name_regex` | `str` | `"(GET|POST) /(users|orders)"` | Operation name pattern match |
| `tag` | `str | list[str]` | `["users", "admin"]` | OpenAPI tags |
| `tag_regex` | `str` | `"users|payments"` | OpenAPI tags pattern match |
| `operation_id` | `str | list[str]` | `"get_user"` | operationId |
| `operation_id_regex` | `str` | `"(get|list)_user"` | operationId pattern match |

!!! note
    Filters are not available for `before_process_path`, `before_load_schema`, `after_load_schema`, `setup`, and `teardown` hooks.

## Phase Compatibility

Not all hooks apply in every phase.

| Hook | Examples | Coverage | Fuzzing | Stateful |
|------|:--------:|:--------:|:-------:|:--------:|
| `before_add_examples` | ✓ | - | - | - |
| `before_generate_{component}` | ✓* | - | ✓ | ✓ |
| `filter_{component}` | ✓* | - | ✓ | ✓ |
| `map_{component}` | ✓* | - | ✓ | ✓ |
| `flatmap_{component}` | ✓* | - | ✓ | ✓ |
| `before_generate_case` | - | - | ✓ | ✓ |
| `filter_case` | - | ✓ | ✓ | ✓ |
| `map_case` | - | ✓ | ✓ | ✓ |
| `flatmap_case` | - | - | ✓ | ✓ |
| `before_call` | ✓ | ✓ | ✓ | ✓ |
| `after_call` | ✓ | ✓ | ✓ | ✓ |
| `after_network_error` | ✓ | ✓ | ✓ | ✓ |
| `after_validate` | ✓ | ✓ | ✓ | ✓ |
| `filter_failure` | ✓ | ✓ | ✓ | ✓ |

**Examples phase** runs test cases built from examples embedded in the schema. \* Component-level hooks apply only to the parts Schemathesis generates to complete an example, such as a required parameter without an example, so `map_headers` can still add a header the example lacks. Values taken from schema examples are never changed by hooks. Case-level hooks (`before_generate_case`, `filter_case`, `map_case`, `flatmap_case`) have no effect on example cases. With [`fill-missing`](configuration.md#phasesexamplesfill-missing) enabled, the case generated for an operation without examples gets all data generation hooks.

**Coverage phase** generates test cases from the schema based on coverage goals (boundary values, required/optional combinations, etc.). Cases bypass the strategy pipeline, so component-level hooks (`filter_query`, `map_headers`, etc.) have no effect.

**Fuzzing phase** uses Hypothesis data generation and respects all data generation hooks.

**Stateful phase** drives multi-step API workflows using a Hypothesis state machine. Each transition generates a new test case via the same strategy as the fuzzing phase, so all data generation hooks apply.

Schema-level hooks (`before_load_schema`, `after_load_schema`, `before_process_path`, `before_init_operation`) run during schema loading, before any test phase begins.

## Execution Order

When multiple hooks of the same type are registered, they execute in registration order, except `teardown` hooks, which run last-registered first. For `filter_*` hooks, the value is discarded as soon as any hook returns `False` - subsequent hooks for that event are not called. For `map_*` hooks, each hook receives the output of the previous. For `flatmap_*` hooks, each hook receives the output of the previous, as with `map_*` hooks. Schema-level hooks run before test-level hooks of the same type.

## Extensions

An extension is a class whose methods are hooks. Schemathesis creates one instance for each `st run` or `st fuzz` invocation, or pytest session, and passes it the extension's settings from `schemathesis.toml`.

```python
# status_log.py
import schemathesis
from schemathesis.engine import events


@schemathesis.extension("status-log")
class StatusLog:
    def __init__(self, settings):
        self.label = settings.get("label", "default")
        self.statuses = []

    def after_call(self, context, case, response):
        self.statuses.append(response.status_code)

    def handle_event(self, ctx, event):
        if isinstance(event, events.EngineFinished):
            ctx.add_summary_line(f"{self.label}: {len(self.statuses)} responses")
```

```toml
hooks = "status_log"

[extensions.status-log]
label = "staging"
```

The run summary gets a line like `staging: 10 responses`.

- **Loading.** Schemathesis loads an extension when [`hooks`](configuration.md#hooks) lists its module. The table name is the string passed to `@schemathesis.extension`; it does not have to match the module name.
- **Settings.** `__init__` receives the `[extensions.<name>]` table as a dict, or `{}` without one. Raise `ValueError` to reject the settings: Schemathesis prints the message and stops before testing. A table that matches no loaded extension also stops the run.
- **Methods.** Each public method is a hook from this page with that hook's arguments, or `handle_event(self, ctx, event)`, which receives CLI events like a [custom handler](../guides/extending-cli.md). Any other public method or nested class fails when the module loads, so prefix helpers with `_`. [Filters](#conditional-application) are not available; check `context.operation` inside the method.
- **App under test.** For a schema loaded with `from_asgi` or `from_wsgi`, `after_load_schema` receives it with `schema.app` set, before any test request. Assign a wrapped app to `schema.app` there to instrument it; an app assigned later, such as in `setup`, receives no requests. Every request carries the `X-Schemathesis-TestCaseId` header with `case.id`, which ties what the app records to the case passed to `after_call`.
- **Order.** For one hook name, `@schemathesis.hook` functions run first, then extensions in the order their modules load. `teardown` runs in the reverse order.
- **Threads.** With more than one worker, hooks that run for each test case are called from several threads on the same instance. Guard multi-step updates of shared state with a lock.
- **Compiled classes.** Register a class from a compiled module, such as PyO3, with a call: `schemathesis.extension("status-log")(StatusLog)`. In PyO3, hook methods take `&self`.
- **Errors.** Errors name the extension: ``Error in `setup` hook of `status-log` extension``.
- **pytest.** The pytest plugin creates one instance per session, as soon as the class registers, with settings from the nearest `schemathesis.toml`. `handle_event` is not called. With `pytest-xdist`, each worker has its own instance.
- **Limits.** `st replay` ignores extensions: it creates no instance and reads no settings.
