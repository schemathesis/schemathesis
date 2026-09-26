# Baseline

Point Schemathesis at an existing API and you may get hundreds of failures on day one. A baseline records the ones you are not fixing yet, so CI fails only on failures that are new.

## Prerequisites

- A running API and its schema URL, for example `http://localhost:8000/openapi.json`
- A repository where you can commit the baseline file next to `schemathesis.toml`

## Capture what exists today

```bash
uvx schemathesis run http://localhost:8000/openapi.json --baseline schemathesis-baseline.json
```

The file does not exist yet, so this run writes it and reports how many failures it recorded:

```
Baseline:
  Known failures: 0
  Recorded: 7
```

Put the path in `schemathesis.toml` so later runs pick it up on their own:

```toml
baseline = "schemathesis-baseline.json"
```

Commit the file. Every later run loads it, says so in the header, reports the recorded failures and exits `0`; anything else exits `1`.

```bash
uvx schemathesis run http://localhost:8000/openapi.json
```

```
     Baseline:         schemathesis-baseline.json / 7 entries

...

Baseline:
  Known failures: 7
```

Once the file exists it changes only when you ask: a plain run reads it and nothing more, so a regression has to be accepted deliberately.

## Record the failures later runs find

Data is generated at random, so a single run does not meet every failure your API has. Expect the next few runs to keep finding new ones, and fold each batch in with `--baseline-update`. `--continue-on-failure` gets you there faster, because a scenario otherwise stops at the first failure on each operation:

```bash
uvx schemathesis run http://localhost:8000/openapi.json --continue-on-failure --baseline-update
```

`Recorded` tells you when to stop - repeat until a run adds nothing:

```
Baseline:
  Known failures: 8
  Recorded: 0
```

Then commit the file.

## Annotate entries

Entries are yours to edit. Every later `--baseline-update` preserves what you wrote, including fields Schemathesis knows nothing about, stamps `first_seen` once, and refreshes `last_seen` each time it sees the failure again:

```json
{
  "id": "2a5e5b",
  "operation": "POST /orders",
  "check": "not_a_server_error",
  "failure": "ServerError",
  "signature": "500",
  "first_seen": "2026-09-25",
  "last_seen": "2026-09-25",
  "reason": "Division by zero, fix scheduled",
  "ticket": "API-4412",
  "expires": "2026-12-01"
}
```

Do not edit `operation`, `check`, `failure` or `signature`: they identify the failure, and an entry with a changed value stops suppressing it. `id` is derived from those four.

`expires` is the only annotation Schemathesis acts on; `reason`, `ticket` and anything else you add ride along untouched. An entry without `expires` suppresses indefinitely. Past that date the entry stops suppressing, the failure resurfaces as new, and the run fails with the entry named:

```
Baseline:
  Known failures: 4
  Unobserved entries: 2
  Expired entries: 1
    2a5e5b
```

## Drop entries you have fixed

```bash
uvx schemathesis run http://localhost:8000/openapi.json --baseline-prune
```

```
Baseline:
  Known failures: 6
  Pruned: 2
```

An entry is dropped only when the run tested its operation and did not reproduce it. Entries for operations the run never reached are kept, so a filtered run, a smaller budget, or a phase subset cannot delete your baseline by accident.

An entry the run did not reproduce is reported as unobserved rather than fixed:

```
Baseline:
  Known failures: 7
  Recorded: 0
  Unobserved entries: 1
```

Data generation is random, so an unobserved entry usually means the run did not reach that failure, not that it is gone.

## In CI

`--report json` writes the same numbers to the `baseline` key of the report, for a pipeline to gate on:

```json
{
  "known": 6,
  "new": 0,
  "recorded": null,
  "pruned_ids": ["10eb82", "b19a23"],
  "unobserved": 0,
  "known_ids": ["130fa8", "2a5e5b", "4ae0f0", "4cd372", "848cfc", "9445e1"],
  "unobserved_ids": [],
  "expired_ids": []
}
```

See the [CI/CD guide](cicd.md) for the surrounding setup.

## What a baseline cannot hold

- Some failure classes are identified by operation, check and status code alone, so one entry covers every failure of that class on that operation at that status. A different bug with the same shape there is suppressed too.
- Failures from custom checks are identified partly by the file and line of the failing assertion, relative to the directory you run in. Moving the assertion within its file records a new entry.
- Response-time failures are never recorded: they vary with machine load.
- Failures a class-based check raises from `after_run` belong to the run, not to an operation, so they always fail the run. Disable the check to silence one.

## Troubleshooting

**A recorded failure fails the run again**: Its entry expired, or one of its `operation`, `check`, `failure`, `signature` fields was edited. Check the `Expired entries` list in the summary.

**The run does not load the baseline**: The header shows no `Baseline:` line. Check that `baseline` in `schemathesis.toml` points at the file relative to where you run the command, or pass `--baseline` explicitly.
