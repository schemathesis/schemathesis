# Replaying Failures

You ran Schemathesis, it found bugs, and you fixed some of them. Now you want to confirm those exact failures are gone - without re-running a whole campaign and hoping the same inputs come up again.

Schemathesis records every failing case to disk during a run. `st replay` re-sends those exact requests and reports which now pass and which still fail.

## Recording happens automatically

When `st run` or `st fuzz` finds a failing case, it writes that case to a crash file under the project cache directory - `.schemathesis/<project-slug>/cache/crashes/` by default. No flag turns this on.

A single case that broke several checks becomes one crash file per check, all sharing the same case ID. Each crash also stores the schema location and base URL from the run, so replay can reload the schema on its own.

The directory heals itself: when an operation passes and never fails again on a later run, its stale crash files are dropped. The contents always reflect what is still broken.

!!! tip "Add it to `.gitignore`"
    Crash files live alongside the rest of the per-project cache. Ignore `.schemathesis/` unless you deliberately want to share captured failures.

## Replay everything

After changing code or schema, re-check every recorded failure:

```bash
st replay
```

Every case gets a one-line status; the ones that still fail are detailed below under `FAILURES`, exactly as `st run` reports them:

```
Replaying 2 cases from .schemathesis/crashes/

  + FIXED  GET /users
  x FAILED POST /orders

=================================== FAILURES ===================================
_________________________________ POST /orders _________________________________
1. Test Case ID: Xt9Kp2

- Server error

- Undocumented HTTP status code

    Received: 500
    Documented: 200

[500] Internal Server Error:

    `{"error": "unhandled exception"}`

Reproduce with:

    curl -X POST http://127.0.0.1/orders

    st replay Xt9Kp2

========================== 1 fixed, 1 failed in 0.14s ==========================
```

Each case carries one of three outcomes:

| Outcome | Meaning |
|---------|---------|
| `+ FIXED` | Every recorded check now passes. The crash file is deleted unless you pass `--keep`. |
| `x FAILED` | At least one recorded check still fails. Detailed under `FAILURES`. Kept. |
| `! ERROR` | The case could not be replayed - the operation is gone from the schema, or a stateful link no longer resolves. Kept. |

A case can fail several checks at once. When only some of them are fixed, the status line keeps `x FAILED` but lists each check, and only the fixed checks' files are removed:

```
  x FAILED POST /orders

    + not_a_server_error
    + status_code_conformance
    x response_schema_conformance
```

Crash files written by an incompatible Schemathesis version are skipped and left on disk - a matching version may still reproduce them.

## Replay one failure

Every failure in `st run` output ends with a `Reproduce with:` block whose last line replays just that case:

```
Reproduce with:

    curl -X POST http://127.0.0.1/orders

    st replay Xt9Kp2
```

You can also point at a single crash file or a directory of them:

```bash
st replay .schemathesis/myapi/cache/crashes/abc123.json
```

## Replay against a different environment

A crash remembers where its schema lived and which base URL it hit. Override either at replay time:

```bash
st replay --url https://staging.example.com
```

This confirms a fix reached staging before you promote it, or reproduces a CI-captured failure against a local server.

When the original schema location is unreachable - crash files copied off a CI runner, for instance - point replay at the schema yourself:

```bash
st replay --schema-location ./openapi.json
```

## Stateful sequences

A failure found during stateful testing is stored as the whole call chain that led to it, not just the final request. Replay re-runs every step in order, re-extracting linked parameters from each response:

```
  x FAILED DELETE /users/{user_id}

=================================== FAILURES ===================================
_____________________________ DELETE /users/{user_id} _____________________________

     1  POST     /auth/login                                   200 -> 200
     2  POST     /users                                        201 -> 201
     3  GET      /users/{user_id}/profile                      200 -> 200
     4  DELETE   /users/{user_id}                              500 -> 503  ~

1. Test Case ID: Lk4Rt8

- Server error

[503] Service Unavailable:

    `{"error": "service unavailable"}`

Reproduce with:

    curl -X DELETE http://127.0.0.1/users/1

    st replay Lk4Rt8
```

The `~` marks a step whose status or body changed since the crash was recorded. If a link extracts a value from a response that is no longer there, that case reports `! ERROR`.

## Exit codes

- `0` - every replayed case is fixed (or there was nothing to replay)
- `1` - at least one case still fails
- `2` - a case could not be replayed, the schema could not be loaded, or no schema location was available
```
