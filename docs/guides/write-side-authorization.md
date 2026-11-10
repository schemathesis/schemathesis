# Testing Write-Level Authorization

[Object-level authorization](object-level-authorization.md) covers the read side: it replays one user's `GET` as another user and reports the object they should not see. The write side is different, and Schemathesis does not cover it today: the object-level authorization check *never repeats writes*.

That leaves a whole class of bugs untested:

- **Cross-identity writes** - `PUT /orders/{id}` accepts an order that belongs to somebody else, and the change lands in the database.
- **Mass assignment** - `PATCH /users/me` applies the `role` the client sent, so a caller mints its own authority.
- **Non-atomic transitions** - two concurrent requests both apply a one-shot action, such as redeeming a coupon twice.

This guide shows how to build such checks with the public extension API.

## Before you start

You need:

- at least two identities, so one can act as the other - see [Authentication](auth.md) and [Web Fuzzing Commons](auth.md#web-fuzzing-commons);
- a way to observe the effect of a write. Either the response echoes the written values, or a `GET` returns the object afterwards.

The example below uses the response, because it needs nothing but Schemathesis itself.

## Mark the fields that carry authority

A client may send many fields, but only a few of them decide what the caller may do. Name them once, in a hook that runs before every request:

```python
# hooks.py
import schemathesis

#: Fields that decide what the caller may do.
AUTHORITY_FIELDS = {"role", "roles", "owner_id", "owner", "tenant_id", "is_admin", "permissions"}


@schemathesis.hook
def map_body(ctx, body):
    """Send a sentinel in every authority field, so a server that trusts the body is visible."""
    if not isinstance(body, dict):
        return body
    for name in AUTHORITY_FIELDS & body.keys():
        body[name] = "mizan-probe-authority"
    return body
```

```console
$ export SCHEMATHESIS_HOOKS=hooks
$ uvx schemathesis run http://localhost:8000/openapi.json
```

The sentinel is a value no legitimate client sends, so it cannot be mistaken for real data. It is also harmless: a server that ignores the field stores nothing.

## Report the write that was accepted

Now a check that fails when the sentinel comes back:

```python
# hooks.py
import schemathesis


@schemathesis.check
def check_authority_fields_are_not_client_controlled(ctx, response, case):
    """Fail when the server persisted an authority field the client sent."""
    if not (200 <= response.status_code < 300):
        return
    try:
        payload = response.json()
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    for name, value in payload.items():
        if name in AUTHORITY_FIELDS and value == "mizan-probe-authority":
            raise AssertionError(
                f"`{case.operation.label}` applied `{name}` from the request body. "
                "The client decided its own authority."
            )
```

The failure names the operation and the field, and it carries the request that caused it, so it can be replayed.

## Reading the object back

Not every API echoes the written values. When it does not, compare the object before and after the write with two `after_call` hooks, or read it back with a second operation that takes the same id. The same idea applies to a cross-identity write: send the write as one peer, then fetch the object as the other peer and compare the two bodies.

For the write to be attributed at all, the request needs to carry the identity that made it. Schemathesis sends each request as the identity it picked for that operation, and `case.auth_identity` tells you which one that was.

## What this does not cover

- **Concurrency.** Schemathesis sends one request at a time, so a check-then-act race is invisible to it. Reproducing one needs a client that releases two identical requests at the same moment and compares the effects.
- **Writes into somebody else's row.** Detecting it needs to know who owns the row, which means reading the data rather than the response. A database trigger or an audit log gives you that.

Both are implemented in [Mizan](https://github.com/Toufiqtoufiqbo/mizan), a third-party tool built on Schemathesis: it learns which access rules an application follows, attributes every database write to the identity that made it, and reports the breaches with a reproduction and a regression test.
