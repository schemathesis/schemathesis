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

**Success flow:** `before_call` -> HTTP Request -> `after_call` -> Checks -> `after_validate`

**Network error flow:** `before_call` -> HTTP Request -> `after_network_error` -> (re-raises)

`CheckResult` fields: `name` (check name), `status` (`Status.SUCCESS` or `Status.FAILURE`), `failure` (`Failure` instance or `None`).

## Hook Registration

| Scope | Registration Method | Available Hooks |
|-------|-------------------|-----------------|
| **Global** | `@schemathesis.hook` or `@schemathesis.hook("hook_name")` | All hooks |
| **Schema** | `@schema.hooks.hook` or `@schema.hooks.hook("hook_name")` | All hooks except `before_load_schema`, `after_load_schema` |
| **Test** | `@schema.hooks.apply(hook_func)` | All hooks except `before_load_schema`, `after_load_schema` |

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
    Filters are not available for `before_process_path`, `before_load_schema`, and `after_load_schema` hooks.

## Phase Compatibility

Not all hooks apply in every phase.

| Hook | Examples | Coverage | Fuzzing | Stateful |
|------|:--------:|:--------:|:-------:|:--------:|
| `before_add_examples` | ✓ | - | - | - |
| `before_generate_{component}` | - | - | ✓ | ✓ |
| `filter_{component}` | - | - | ✓ | ✓ |
| `map_{component}` | - | - | ✓ | ✓ |
| `flatmap_{component}` | - | - | ✓ | ✓ |
| `before_generate_case` | - | - | ✓ | ✓ |
| `filter_case` | - | ✓ | ✓ | ✓ |
| `map_case` | - | ✓ | ✓ | ✓ |
| `flatmap_case` | - | - | ✓ | ✓ |
| `before_call` | ✓ | ✓ | ✓ | ✓ |
| `after_call` | ✓ | ✓ | ✓ | ✓ |
| `after_network_error` | ✓ | ✓ | ✓ | ✓ |
| `after_validate` | ✓ | ✓ | ✓ | ✓ |

**Examples phase** runs test cases embedded directly in the schema. Cases bypass the strategy pipeline, so data generation hooks have no effect on them.

**Coverage phase** generates test cases from the schema based on coverage goals (boundary values, required/optional combinations, etc.). Cases bypass the strategy pipeline, so component-level hooks (`filter_query`, `map_headers`, etc.) have no effect.

**Fuzzing phase** uses Hypothesis data generation and respects all data generation hooks.

**Stateful phase** drives multi-step API workflows using a Hypothesis state machine. Each transition generates a new test case via the same strategy as the fuzzing phase, so all data generation hooks apply.

Schema-level hooks (`before_load_schema`, `after_load_schema`, `before_process_path`, `before_init_operation`) run during schema loading, before any test phase begins.

## Execution Order

When multiple hooks of the same type are registered, they execute in registration order. For `filter_*` hooks, the case is discarded as soon as any hook returns `False` — subsequent hooks for that event are not called. For `map_*` hooks, each hook receives the output of the previous. For `flatmap_*` hooks, each hook receives the output of the previous, as with `map_*` hooks. Schema-level hooks run before test-level hooks of the same type.
