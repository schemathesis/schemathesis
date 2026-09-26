# Replaying Failures

You ran Schemathesis, it found bugs, and you fixed some of them. Now you want to confirm those exact failures are gone - without re-running a whole campaign and hoping the same inputs come up again.

Schemathesis records every failing case to disk during a run. `st replay` re-sends those exact requests and reports which now pass and which still fail.

## Prerequisites

- A previous `st run` or `st fuzz` run that found failures
- The API reachable at the URL the run used, or at a URL you pass with `--url`
- Run `st replay` from the directory where you ran `st run`, with the same `schemathesis.toml`. Crash files live in a cache directory relative to the working directory, so from anywhere else `st replay` prints `No crash files found.`

## Recording happens automatically

When `st run` or `st fuzz` finds a failing case, it writes that case to a crash file under the project cache directory - `.schemathesis/<project-slug>/cache/crashes/` by default, where the slug is `default` unless your config names the project. Set `cache.directory` in `schemathesis.toml` to move it. No flag turns recording on.

A single case that broke several checks becomes one crash file per check, all sharing the same case ID. Each crash also stores the schema location and base URL from the run, so replay can reload the schema on its own. Crash files for operations that pass on a later run are removed.

!!! tip "Add it to `.gitignore`"
    Crash files live alongside the rest of the per-project cache. Ignore `.schemathesis/` unless you deliberately want to share captured failures.

## Replay everything

After changing code or schema, re-check every recorded failure:

```bash
uvx schemathesis replay
```

Every case gets a one-line status; the ones that still fail are detailed below under `FAILURES`, exactly as `st run` reports them:

```
Replaying 2 cases from .schemathesis/default/cache/crashes

  + FIXED  GET /users
  x FAILED POST /orders

=================================== FAILURES ===================================
_________________________________ POST /orders _________________________________
1. Test Case ID: D5eIJE

- Server error

- Undocumented HTTP status code

    Received: 500
    Documented: 200, 422

[500] Internal Server Error:

    `{"detail":"division by zero"}`

Reproduce with:

    curl -X POST -H 'Content-Type: application/json' -d '{"item": "", "quantity": false}' http://localhost:8000/orders

    st replay D5eIJE

Removed 2 crash files (pass --keep to retain).

========================== 2 fixed, 2 failed in 0.03s ==========================
```

The final line counts checks, not cases: each case above had two failing checks.

Each case carries one of four outcomes:

| Outcome | Meaning |
|---------|---------|
| `+ FIXED` | Every recorded check passed on three consecutive replays. The crash file is deleted unless you pass `--keep`. |
| `x FAILED` | At least one recorded check still fails. Detailed under `FAILURES`. Kept. |
| `? FLAKY` | A check passed on some replays and failed on others, so the failure is intermittent. Each check line shows how many replays passed. Detailed under `FAILURES`. Kept. |
| `! ERROR` | The replay could not verify the case: the operation is gone from the schema, the request could not be sent, a credential masked in the crash file was not supplied, or the API rejected the replay's credentials. Kept. |

A passing replay does not end the attempts: a case is only `FIXED` after three clean replays. One that fails right away stops after the first, so verifying a known-broken crash costs no extra requests.

```
  ? FLAKY  GET /report

    ? not_a_server_error       passed 2 of 3 replays
    ? status_code_conformance  passed 2 of 3 replays
```

A case can fail several checks at once. When only some of them are fixed, the status line keeps `x FAILED` but lists each check, and only the fixed checks' files are removed:

```
  x FAILED POST /orders

    + not_a_server_error
    x status_code_conformance
```

Here the server stopped returning `500` but answers with an undocumented `409`, so the `not_a_server_error` crash file is removed and the `status_code_conformance` one is kept.

Crash files written by an incompatible Schemathesis version are skipped and left on disk - a matching version may still reproduce them.

## Replay one failure

Every failure in `st run` output ends with a `Reproduce with:` block whose last line replays just that case:

```
Reproduce with:

    curl -X POST -H 'Content-Type: application/json' -d '{"item": "", "quantity": false}' http://localhost:8000/orders

    st replay D5eIJE
```

You can also point at a single crash file or a directory of them:

```bash
uvx schemathesis replay .schemathesis/default/cache/crashes/POST_orders_not_a_server_error_c64d116c.json
```

## Replay against a different environment

A crash remembers where its schema lived and which base URL it hit. Override either at replay time:

```bash
uvx schemathesis replay --url https://staging.example.com
```

This confirms a fix reached staging before you promote it, or reproduces a CI-captured failure against a local server.

When the original schema location is unreachable - crash files copied off a CI runner, for instance - point replay at the schema yourself:

```bash
uvx schemathesis replay --schema-location ./openapi.json
```

## Credentials

Crash files follow the `output.sanitization` rules. By default, values of headers, query parameters and cookies with sensitive names, such as `Authorization` or `X-API-Key`, are replaced with `[Filtered]`. Request and response bodies are stored as they were, and with sanitization disabled (`--output-sanitize false`) so is every other value, so keep the crash directory out of version control and shared storage. On replay, Schemathesis fills filtered values from the same sources as `st run`: `[headers]` and `[auth]` in `schemathesis.toml`, auth providers registered with `@schemathesis.auth()` in your hooks, and command-line options:

```console
uvx schemathesis replay -H "Authorization: Bearer $TOKEN"
uvx schemathesis replay --auth user:pass
uvx schemathesis replay --auth-wfc ./auth.yaml --auth-wfc-user admin
```

Command-line credentials take precedence over the config file.

Without the right credentials, a case is never reported as fixed. If a masked value is not supplied and every check passes, the case shows as `! ERROR` with a note naming the missing value, and the crash file is kept:

```
  ! ERROR  GET /protected

    `Authorization` header was masked in the crash file; provide it via config, -H, --auth or an auth hook
```

A replay the API rejects is never reported as fixed either. When a step gets `401` or `403` but the recorded request did not, for example because a token expired or `-H` carries a wrong one, the case shows as `! ERROR` and the crash file is kept:

```
  ! ERROR  GET /protected

    replay was not authenticated: the API answered 401 where the recorded request got 500; check the credentials passed via config, -H, --auth or an auth hook
```

In a stateful sequence the note names the step, such as `at step 1`. If another check still fails, the case stays `x FAILED` and each passing check shows a row such as `! not_a_server_error  replay was not authenticated (401)`. Crash files whose recorded response was itself `401` or `403` replay as usual, so authentication failures can still be verified as fixed.

Masked values in path parameters or request bodies cannot be supplied. Such crash files are never deleted automatically, even when the replay passes.

## Stateful sequences

A failure found during stateful testing is stored as the whole call chain that led to it, not just the final request. Replay re-runs every step in order, re-extracting linked parameters from each response:

```
Replaying 1 case from dnNCj2

  x FAILED GET /items/{itemId}

=================================== FAILURES ===================================
_____________________________ GET /items/{itemId} ______________________________

     1  POST  /items           201
     2  GET   /items/{itemId}  500 -> 503  ~  itemId = "5a353598a15f ..." (was "b37dc415b542 ...", from step 1)

1. Test Case ID: dnNCj2

- Server error

- Undocumented HTTP status code

    Received: 503
    Documented: 200, 404, 422

[503] Service Unavailable:

    `{"detail":"item lookup failed"}`

Reproduce with:

    curl -X POST -H 'Content-Type: application/json' -d '{"name": "s"}' http://localhost:8000/items
    curl -X GET http://localhost:8000/items/5a353598a15f4738986c4a26af66022f

    st replay dnNCj2
```

A `~` marks a step whose status or body changed since the crash was recorded; `500 -> 503` shows the recorded and the current status. Values a step takes from an earlier response are listed as `name = replayed (was recorded, from step N)` (here the `itemId` of the item the replay just created, taken from step 1); an unchanged value shows only `(from step N)`, and a new ID alone does not count as a change. The curls use the replayed values, so they run against the current server state. If a linked value is no longer in the response, replay sends the recorded value and marks it `(recorded, not re-extracted)`.

## Exit codes

- `0` - every replayed case is fixed, or there was nothing to replay
- `1` - at least one case is `FAILED` or `FLAKY`
- `2` - a case could not be replayed (`ERROR`), the schema could not be loaded, no schema location was available, or no crash file matches the given case ID
- `130` - the replay was interrupted with Ctrl+C

When a run has both kinds of problems, `1` wins over `2`.

## Troubleshooting

**`No crash files found.`**: `st replay` looks in the cache directory relative to the current directory. Change to the directory where you ran `st run`, or pass the crash directory as an argument.

**`Error: no crash file found for case ID: ...`**: The crash was fixed and removed by an earlier replay or run, or you are in a different directory.

**`Error: cannot replay without a schema. Pass --schema-location <url-or-path>.`**: The crash directory has no recorded schema location, for example after copying only the crash files. Pass `--schema-location`.
