# Optimizing Schemathesis for Maximum Bug Detection

This guide shows how to configure Schemathesis for maximum bug detection and API coverage.

## When to Use This Configuration

Use these settings when:

- Preparing for production releases
- Conducting security assessments
- Time allows for extensive testing

For fast development feedback, stick with defaults.

To measure Schemathesis itself — comparing it with other tools, or checking whether a change helped — see [Benchmarking Schemathesis](benchmarking.md). That configuration differs from this one: it optimizes for numbers that are comparable and reproducible, not for findings a developer can act on.

## Recommended Configuration

```bash
schemathesis run <schema_url> \
  --max-examples 1000 \
  --continue-on-failure
```

## Key Configuration Changes

### `--max-examples 1000` (default: 100)
Higher example counts improve bug detection and coverage. Adjust based on your time budget - more examples find more issues.

### `--max-time SECONDS`
Bounds the run by the clock rather than by example count: the fuzzing and stateful phases keep drawing new cases until the budget is spent. Use it whenever you have a wall-clock budget, since `--max-examples` stops as soon as the count is reached, which can be a fraction of the time available.

### `--continue-on-failure` (default: stop an operation at its first failure)
Keeps testing an operation after it produces a failure. The default supports fast development cycles (find bug -> fix -> repeat), but it leaves most of that operation's budget unspent, so a second bug behind the first one is never found.

### `--generation-maximize response_time`
Guides generation toward inputs that maximize API response time — useful for finding performance bottlenecks and latency-sensitive vulnerabilities.

See [Targeted Testing](targeted.md) for more details and custom metric examples.

### `--suppress-health-check filter_too_much,too_slow`
Complex schemas can trigger Hypothesis health checks by spending too much time filtering invalid inputs. When this happens, health checks may fire and abort a test. For long release-gate runs, suppressing `filter_too_much` and `too_slow` is reasonable.

!!! warning
    Firing health checks is worth investigating locally—they may indicate a schema correctness issue.

For security assessments, pair these settings with a curated wordlist via [Fuzz Dictionaries](fuzz-dictionary.md) to feed SQL injection, XSS, or other classic payloads into generation.
