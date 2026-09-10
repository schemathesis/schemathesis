# Baseline

Point Schemathesis at an existing API and you may get hundreds of failures on day one. A baseline records the ones you are not fixing yet, so CI fails only on failures that are new.

## Capture what exists today

```bash
schemathesis run http://localhost:8080/openapi.json --baseline schemathesis-baseline.json
```

The file does not exist yet, so this run writes it. Put the path in `schemathesis.toml` so later runs pick it up on their own:

```toml
baseline = "schemathesis-baseline.json"
```

Commit the file. Every later run loads it, says so in the header, reports the recorded failures and exits `0`; anything else exits `1`.

```bash
schemathesis run http://localhost:8080/openapi.json
```

```
Baseline:    schemathesis-baseline.json / 47 entries

...

Baseline:
  Known failures: 47

=========================== No issues found in 12.03s ==========================
```

Once the file exists it changes only when you ask: a plain run reads it and nothing more. That is what keeps CI honest - a regression has to be accepted deliberately, not absorbed by the run that found it.

## One run is not enough

Data is generated at random, so a single run does not meet every failure your API has. Expect the next few runs to keep finding new ones, and fold each batch in with `--baseline-update`. `--continue-on-failure` gets you there faster, because a scenario otherwise stops at the first failure on each operation:

```bash
schemathesis run http://localhost:8080/openapi.json --continue-on-failure --baseline-update
```

Against a 33-operation demo API this recorded 37 entries in one run; without the flag it took four runs to reach the same set.

`Recorded` tells you when to stop - repeat until a run adds nothing:

```
Baseline:
  Known failures: 37
  Recorded: 0
```

Then commit the file.

## Annotate entries

Entries are yours to edit. Every later `--baseline-update` preserves what you wrote, including fields Schemathesis knows nothing about, and stamps `first_seen` once and refreshes `last_seen` each time it sees the failure again:

```json
{
  "id": "f3a91c",
  "operation": "GET /users/{userId}",
  "check": "positive_data_acceptance",
  "failure": "RejectedPositiveData",
  "signature": "409",
  "first_seen": "2026-09-10",
  "last_seen": "2026-09-14",
  "reason": "Legacy conflict semantics on the v1 read path",
  "ticket": "API-4412",
  "expires": "2026-12-01",
  "owner": "payments-team"
}
```

`operation`, `check`, `failure` and `signature` are what an entry matches on; change any of them and it stops suppressing. `id` is derived from those four, and `first_seen` / `last_seen` are stamped for you.

Everything else is yours. `expires` is the only annotation Schemathesis acts on; `reason`, `ticket` and anything else you invent ride along untouched.

`expires` is the deadline you set on a suppression; nothing sets it for you, and an entry without one suppresses indefinitely. Past that date the entry stops suppressing, the failure resurfaces as new, and the run fails with the entry named:

```
Baseline:
  Expired entries: 1
    f3a91c
```

## Drop entries you have fixed

```bash
schemathesis run http://localhost:8080/openapi.json --baseline-prune
```

```
Baseline:
  Known failures: 41
  Pruned: 3
```

An entry is dropped only when the run tested its operation and did not reproduce it. Entries for operations the run never reached are kept - a filtered run, a smaller budget, or a phase subset cannot delete your baseline by accident.

This is also why an unobserved entry is reported rather than treated as fixed:

```
Baseline:
  Known failures: 41
  Unobserved entries: 6
```

Six entries did not come up. Data generation is random, so that is not evidence they are gone. Other tools call these "unmatched" or "unused" suppressions and treat them as stale; here they usually mean the run simply did not reach them.

## In CI

`--report json` carries the same numbers for a pipeline to gate on:

```json
{
  "baseline": {
    "known": 41,
    "new": 0,
    "recorded": null,
    "unobserved": 6,
    "known_ids": ["f3a91c", "..."],
    "unobserved_ids": ["9b2e04", "..."],
    "expired_ids": [],
    "pruned_ids": null
  }
}
```

See the [CI/CD guide](cicd.md) for the surrounding setup.

## What an entry matches

A failure is identified by its operation, the check that raised it, the failure class, and one discriminating value - a status code, a JSON pointer into the schema, a header name. Generated payloads and case ids are deliberately excluded, so entries survive a change of seed.

Two consequences worth knowing:

- Some failure classes key on the status code alone, so one entry covers every failure of that class on that operation at that status. A second, genuinely different bug of the same shape there would be absorbed.
- Custom checks are keyed partly by the file and line the assertion was raised at, relative to the directory you run in. Entries travel between checkouts, but moving the assertion within its file writes a new one.

Response-time failures are never recorded: they flap with machine load, so an entry for one would never settle.

Failures a class-based check raises from `after_run` belong to the run, not to an operation, so they cannot be recorded either. They always fail the run; disable the check to silence one.
