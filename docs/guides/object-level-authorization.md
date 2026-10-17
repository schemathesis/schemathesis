# Testing Object-Level Authorization

Find endpoints where one user can read another user's objects, such as `GET /orders/{id}` returning someone else's order (OWASP API1, Broken Object Level Authorization).

## Before you start

Schemathesis tests a read only when it knows which user owns the object. An object counts as the user's own when:

- the user created it: a create such as `POST /orders` returned its id, or the user chose the id itself, as in `PUT /orders/{id}` or a title sent in the create body, or
- a list the user requested returned it, and the same list requested by another peer does not.

So the API needs an operation that returns or accepts ids, either a create or a per-user list, plus a `GET` that takes the id as a path or query parameter. You don't need to link them. The coverage and fuzzing phases reuse ids from earlier responses in later requests, and [stateful testing](stateful-testing.md) chains `POST /orders` into `GET /orders/{id}`; either one is enough.

## Set up two users

The check needs credentials for at least two users. List them in a [Web Fuzzing Commons](auth.md#web-fuzzing-commons) auth document:

```json
{
  "auth": [
    {"name": "alice", "fixedHeaders": [{"name": "Authorization", "value": "Bearer alice-token"}]},
    {"name": "bob", "fixedHeaders": [{"name": "Authorization", "value": "Bearer bob-token"}]}
  ]
}
```

Entries can also log in through an endpoint instead of fixed headers; see [Web Fuzzing Commons](auth.md#web-fuzzing-commons) for `loginEndpointAuth`. Then list the users whose objects must stay private to each other as `peers`, by their `name`:

```toml
# schemathesis.toml
[auth.wfc]
path = "auth.json"
peers = ["alice", "bob"]
```

```console
$ uvx schemathesis run http://localhost:8000/openapi.json
```

You can set `peers` only in `schemathesis.toml`, and you can't combine `[auth.wfc]` with `[auth.basic]` or `[auth.openapi.*]`. If you authenticate another way today, move those credentials into the WFC document.

Each operation runs as the identity Schemathesis [picks for it](auth.md#web-fuzzing-commons), or as the `user` set under `[auth.wfc]`. Only reads sent as one of the `peers` are probed: when that user reads one of their own objects with a `GET`, Schemathesis repeats the request as each other peer. It never repeats writes.

## Reading a failure

```text
- ST402 Object-level authorization bypass

    `bob` received the `Order` that `alice` owns

    Owner: GET /api/orders/1 as alice -> 200
    Peer:  GET /api/orders/1 as bob -> 200 (equivalent body)
```

`bob` got a `2xx` response with the same object: the same id, and the same values in the other fields both bodies have. Before reporting, Schemathesis rules out objects that are public or shared on purpose. The check passes when:

- `bob` gets different values, such as masked fields, or only the id
- an unauthenticated request gets the same body
- `bob` can see the full object in a list `bob` may request

If the object is shared by design in a way the API does not expose through a list, such as a team resource, exclude the operation from the check:

```toml
[[operations]]
include-path = "/api/teams/{team_id}"
checks.object_level_authorization.enabled = false
```

In a [WFC report](../reference/cli.md#-report-wfc-path-filename), these failures use the Schemathesis-specific fault code `906`.

## When nothing is reported

A clean run covers only the objects Schemathesis could tie to a user. If it ties none, it sends no extra requests and reports nothing. Check for:

- No `peers` under `[auth.wfc]`: the check does not run at all. When you select it with `--checks object_level_authorization`, Schemathesis warns about this before loading the schema.
- Failing creates, such as `POST /orders` always returning `422`. Fix those first; see [Triaging Failures](triage.md).
- A create response without the id, in an API with no list operation.
- Responses whose schemas declare no fields, so ids in them are never tied to a user.
- Reads that do not run as a peer: the operation accepts requests without credentials, or `user` under `[auth.wfc]` names someone missing from `peers`.
- A public `GET`: the schema sets a top-level `security`, and the operation overrides it with `security: []`. Schemathesis skips those.

Each probed read costs one request per other peer. An id taken from a list adds one list request per peer. When a peer receives the object, Schemathesis also sends an unauthenticated request and one request per list that may contain it. See the [check reference](../reference/checks.md#object_level_authorization) for the exact comparison rules.
