# Optimizing Schemathesis for Maximum Bug Detection

This guide shows how to configure Schemathesis for maximum bug detection and API coverage.

## When to Use This Configuration

Use these settings when:

- Preparing for production releases
- Conducting security assessments
- Comparing tool effectiveness
- Time allows for extensive testing

For fast development feedback, stick with defaults.

## Recommended Configuration

```bash
schemathesis run <schema_url> \
  --max-examples 1000 \
  --continue-on-failure
```

## Key Configuration Changes

### `--max-examples 1000` (default: 100)
Higher example counts improve bug detection and coverage. Adjust based on your time budget - more examples find more issues.

### `--continue-on-failure` (default: stop on first failure)
Continues testing all operations even when bugs are found. The default supports fast development cycles (find bug -> fix -> repeat), but for thorough testing you want to test all operations regardless of failures.

### `--generation-maximize response_time`
Guides generation toward inputs that maximize API response time — useful for finding performance bottlenecks and latency-sensitive vulnerabilities.

See [Targeted Testing](targeted.md) for more details and custom metric examples.

### `--suppress-health-check filter_too_much,too_slow`
Complex schemas can trigger Hypothesis health checks by spending too much time filtering invalid inputs. When this happens, health checks may fire and abort a test. For long release-gate runs, suppressing `filter_too_much` and `too_slow` is reasonable.

!!! warning
    Firing health checks is worth investigating locally—they may indicate a schema correctness issue.

!!! info "For Researchers"
    When running multiple iterations, prefer higher `--max-examples` with fewer iterations rather than low examples with many iterations. For example, 2 runs of 500 examples each are more effective than 10 runs of 100 examples because Hypothesis can better explore the input space in longer runs.
