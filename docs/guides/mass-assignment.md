# Testing for Mass Assignment

Find writes that store fields clients must not set, such as `POST /users` accepting `"role": "admin"` or `PATCH /accounts/{id}` accepting `"balance": 1000000` (OWASP API3, Broken Object Property Level Authorization).

## Before you start

The `mass_assignment` check is off by default: it sends writes that try to give the caller privileges. Run it against a dev or staging environment with test accounts, never production.

The check probes `POST`, `PUT` and `PATCH` operations with JSON object bodies. It confirms a finding by reading the resource back, so it finds the most when the API has a `GET` on the item path, such as `GET /users/{id}` next to `POST /users`. Without one, it falls back to the write response (see [How a finding is confirmed](#how-a-finding-is-confirmed)), and an API with an unusual read path can [name it](#reads-at-another-path).

The more the schema says about protected fields, the more the check finds. Mark fields the server owns as `readOnly`, or leave them out of the request schema while keeping them in the response schema:

```yaml
User:
  type: object
  properties:
    id:       {type: integer, readOnly: true}
    name:     {type: string}
    role:     {type: string, enum: [user, admin], readOnly: true}
    is_admin: {type: boolean, readOnly: true}
```

## Run it

```console
$ uvx schemathesis run http://localhost:8000/openapi.json --checks mass_assignment
```

`--checks mass_assignment` runs only this check. To add it to the default checks instead, enable it in `schemathesis.toml`:

```toml
# schemathesis.toml
[checks.mass_assignment]
enabled = true
```

The probes carry the same credentials, headers and proxy settings as the rest of the run (`--header`, `--auth`, `[auth]`, `--proxy`). Use the account with the fewest privileges: a finding means that account could raise its own privileges.

## Which fields are injected

For each successful write, Schemathesis builds one copy of the request with extra fields:

| Origin | Example | Value |
|---|---|---|
| `read-only`: `readOnly` in the request schema | `role` | `"admin"`, or another enum member |
| `response-only`: in the response schema, not the request schema | `verified` | the opposite of the stored boolean |
| `undocumented`: common privilege names the schema does not mention | `is_admin`, `roles`, `balance` | `true`, `["admin"]`, `987654321` |
| `configured`: listed under [`fields`](#inject-your-own-fields) | `plan` | the value you set |
| `hook`: added by a [hook](#adjust-probes-in-python) | anything | the value the hook sets |

Identifiers (`id`, `*_id`, `*Id`, path parameters) are never injected automatically, and neither are fields the request already sets. Strings get a marker like `st-ma-1a2b3c4d`, numbers `987654321`, dates `2001-02-03`. A field with a `pattern`, `const` or a range the marker does not fit is skipped.

Responses that wrap the resource, like `{"article": {...}}` or `{"data": {...}}`, are unwrapped: the nested object counts as the resource when it shares more fields with the request body than the top level does. Deeper or ambiguous wrapping needs [`response-envelope`](#nested-request-and-response-bodies).

## How a finding is confirmed

Schemathesis records the value the server stored for the original request, from the write response or a `GET` sent before the probe. After the probe, it reads the resource back with a `GET`. A field is reported when the read returns the injected value and either:

- the original request stored a different value, or
- the injected value is one no server picks by itself, such as the string marker or `987654321`.

So a boolean or enum field whose original value is unknown is not reported, even if the read shows the injected value: `true` may simply be the default.

Without a `GET`, or when it answers `404` right after the write (e.g. a lagging replica), the write response decides, and only when the response to the original request already held a different value for the field. An API that echoes the request body back without storing it does not fail.

Values are compared the way servers commonly re-encode them: `1` and `"true"` match `true` (SQLite has no boolean type), `"987654321"` matches `987654321`, timestamps match with or without milliseconds and offsets, and email addresses match case-insensitively.

## Reading a failure

```text
- ST403 Mass assignment

    The API stored `role`, `is_admin`, which clients must not set

    Write: POST /users
    Read:  GET /users/8

    role      "admin"  (read-only, seen in GET /users/8)
    is_admin  true     (read-only, seen in GET /users/8)
```

The reproduction commands in the report are the probe itself, with the injected fields in the body, followed by the read. Fix the API by binding request bodies to an allowlist of writable fields, then ignore or reject the rest with `400` or `422`.

If a field is meant to be writable, declare it in the request schema without `readOnly`, or list it under [`exclude-fields`](#skip-fields).

## Adapting the check to your API

Every option goes under `[checks.mass_assignment]`, and every one can be set for a single operation under `[[operations]]`:

```toml
# schemathesis.toml
[[operations]]
include-name = "POST /accounts"
checks.mass_assignment.read = "GET /lookup/accounts/{uuid}"
checks.mass_assignment.id-field = "data.uuid"
```

### Inject your own fields

The built-in names are generic. Add the fields that grant privileges in your domain, with the value an attacker would want:

```toml
[checks.mass_assignment]
enabled = true
fields = { plan = "enterprise", credit_limit = 1000000, org_id = 4242 }
```

Configured fields are injected even when they look like identifiers, so `org_id` above tests moving an object into another organization. String and number values count without a baseline; pick ones the server would never choose.

### Skip fields

```toml
[checks.mass_assignment]
exclude-fields = ["display_order", "nickname"]
privilege-fields = false   # don't inject `is_admin`, `role`, ... unless the schema mentions them
```

### Nested request and response bodies

For JSON:API and other formats that nest the object being written, say where it is with dotted paths:

```toml
[checks.mass_assignment]
# {"data": {"type": "accounts", "attributes": {"name": "..."}}}
request-envelope = "data.attributes"
# {"data": {"id": "...", "attributes": {...}}}
response-envelope = "data.attributes"
```

`response-envelope = ""` turns off unwrapping and compares top-level fields.

### Reads at another path

When the resource is read somewhere other than the item path below the write, name the read and where the write response holds the identifier:

```toml
[[operations]]
include-name = "POST /accounts"
checks.mass_assignment.read = "GET /lookup/accounts/{uuid}"
checks.mass_assignment.id-field = "data.uuid"
```

Without `id-field`, the identifier comes from the `Location` header, then from an `id` or same-named field in the body.

### Limit side effects

```toml
[checks.mass_assignment]
max-attempts = 1    # rejected probes per operation before giving up; default 3
cleanup = false     # keep resources the probes created, e.g. to inspect them
```

## Adjust probes in Python

For anything the options can't express, a `before_mass_assignment_probe` hook sees each probe before it is sent. `probe.fields` holds the fields to inject, `probe.case` the request that will carry them, and `probe.original` the successful request it copies:

```python
import schemathesis


@schemathesis.hook
def before_mass_assignment_probe(context, probe):
    # Never repeat payments.
    if context.operation.path.startswith("/payments"):
        probe.skip = True
        return
    # The API wants a fresh CSRF token on every write.
    probe.case.headers["X-CSRF-Token"] = fetch_csrf_token()
    # Domain-specific fields, and one the API may legitimately accept.
    probe.fields["subscription"] = "lifetime"
    probe.fields.pop("verified", None)
```

Save it as `hooks.py` and load it with `export SCHEMATHESIS_HOOKS=hooks` or the `hooks` config option; see [Extending Schemathesis](extending.md).

## Side effects

- Each write operation gets one accepted probe per run; after `max-attempts` rejected probes, it is not probed again.
- A resource a `POST` probe created is deleted afterwards, when the schema has a `DELETE` on its item path and `cleanup` is on.
- A `PUT` or `PATCH` probe changes the same resource as the request it copies and is **not** undone. A finding means that resource may now hold the injected values, such as an elevated role.
- A create allowed once per account, such as "join" or "create my profile", answers the probe with `409` and can't be tested this way.

## Limits

- Fields are injected at one level: the top of the body, or the object `request-envelope` names.
- Ownership fields such as `owner_id` are injected only when listed under `fields`: proving a reassignment needs a real id of another user.
- The probes run in every phase that sends writes. In stateful testing, a probe is not part of the scenario chain.
