# Hooks Reference

## Schema-Level Hooks

| Hook                        | Signature                                     | When / Why                                                                                       |
| --------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| **before\_load\_schema**    | `(context, raw_schema: dict) -> None`         | Modify the raw schema before parsing (e.g., inject `const` values, patch syntax).                |
| **after\_load\_schema**     | `(context, schema: BaseSchema) -> None`       | Inspect or mutate the loaded schema object (e.g., add links, override formats).                  |
| **before\_process\_path**   | `(context, path: str, methods: dict) -> None` | Tweak a specific endpoint’s definition (e.g., set parameter defaults from your DB).              |
| **before\_init\_operation** | `(context, operation: APIOperation) -> None`  | Adjust an `APIOperation` before building strategies (e.g., restrict enum choices, set defaults). |
| **before\_add\_examples**   | `(context, examples: list[Case]) -> None`     | Append explicit `Case` instances before the `examples` phase.                              |

## Data-Generation Hooks

## Request-Lifecycle Hooks

Mutate or inspect the test case and HTTP request flow.

| Hook                      | Signature                                           | When / Why                                                                                |
| ------------------------- | --------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| **before\_call**          | `(context, case: Case) -> None`                     | Right before sending the HTTP request; modify `case` (e.g., add headers, tweak query).    |
| **process\_call\_kwargs** | `(context, case: Case, kwargs: dict) -> dict`       | Alter keyword args to `case.call(**kwargs)` (e.g., disable redirects, set timeouts).      |
| **after\_call**           | `(context, case: Case, response: Response) -> None` | Immediately after a successful call; inspect or mutate `response` before built-in checks. |

---

## Conditional Application

Apply hooks only to matching API operations:

```python
@schemathesis.hook.apply_to(path_regex=r"^/users/")  
                  .skip_for(method="POST", tag="admin")
def map_headers(context, headers):
    headers = headers or {}
    headers["X-User-Test"] = "true"
    return headers
```

* **apply\_to(...)**: target operations matching **all** given conditions (AND logic).
* **skip\_for(...)**: exclude operations matching **any** given conditions (OR logic).

**Supported filters:** `path`, `path_regex`, `method`, `name`, `tag`, `operation_id` (accepts string, list, or `<field>_regex`).
