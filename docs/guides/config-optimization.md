# Optimizing Schemathesis for Maximum Bug Detection

This guide shows how to configure a long Schemathesis run that finds as many bugs as possible, for example before a release or during a security assessment. For fast feedback during development, keep the defaults.

To measure Schemathesis itself - comparing it with other tools, or checking whether a change helped - see [Benchmarking Schemathesis](benchmarking.md). That configuration differs from this one: it optimizes for numbers that are comparable and reproducible, not for findings a developer can act on.

## Prerequisites

- Schemathesis installed or available via `uvx`
- A time budget for the run, for example 10 minutes

## Recommended Configuration

```bash
uvx schemathesis run http://localhost:8000/openapi.json \
  --max-time 600 \
  --continue-on-failure
```

The same settings in `schemathesis.toml`:

```toml
max-time = 600
continue-on-failure = true
```

The run ends with a summary like this (a 10-second budget against a single operation):

```
Failures:
  ❌ Server error: 1
  ❌ Undocumented HTTP status code: 1

Test cases:
  364 generated, 1 found 2 unique failures
```

## What each setting changes

### `--max-time SECONDS`

Bounds the run by the clock rather than by example count: the fuzzing and stateful phases run again and again, drawing new cases, until the budget is spent. Without it, the run ends once each operation has generated `--max-examples` cases, which can be a fraction of the time available.

### `--continue-on-failure` (default: stop an operation at its first failure)

Keeps testing an operation after it produces a failure. The default supports fast development cycles (find bug -> fix -> repeat), but it leaves most of that operation's budget unspent, so a second bug behind the first one is never found.

## Optional settings

### `--max-examples N` (default: 100)

Sets how many test cases the fuzzing phase generates per operation. Without `--max-time`, raise it to make the run longer. With `--max-time`, it only sets the size of each pass, so the default is usually fine.

### `--generation-maximize response_time`

Guides generation in the fuzzing and stateful phases toward inputs that make the API respond more slowly. Add it when you look for performance problems. See [Targeted Testing](targeted.md) for details and custom metrics.

### `--suppress-health-check filter_too_much,too_slow`

Complex schemas can trigger Hypothesis health checks by spending too much time filtering invalid inputs, and a health check that fires aborts the test for that operation. Suppress `filter_too_much` and `too_slow` for long release-gate runs if you see these errors.

!!! warning
    Investigate health check failures locally before suppressing them; they may point to a schema correctness issue.

For security assessments, pair these settings with a curated wordlist via [Fuzz Dictionaries](fuzz-dictionary.md) to feed SQL injection, XSS, or other classic payloads into generation.

## Troubleshooting

**The run ends long before `--max-time`.** Only the fuzzing and stateful phases repeat until the budget is spent. If most operations are skipped or error out early, check the per-phase results in the summary.

**Health check errors for some operations.** Add `--suppress-health-check filter_too_much,too_slow`, then check whether the schema for those operations has contradictory constraints.
