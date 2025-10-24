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

**Flow:** `before_call` → HTTP Request → `after_call` → Checks

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
