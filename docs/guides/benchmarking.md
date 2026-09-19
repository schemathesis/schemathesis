# Benchmarking Schemathesis

This guide is for measuring Schemathesis itself - comparing it with other API testing tools, or checking whether a change to the tool or its configuration helped.

If you are testing your own API and want to find more bugs in it, see [Optimizing for Maximum Bug Detection](config-optimization.md) instead. The two configurations differ because the goals differ: that guide tunes a run so it reports useful findings to a developer, while this one tunes a run so the numbers it produces are comparable and reproducible.

## Recommended Configuration

```bash
schemathesis run <schema_url> \
  --max-time 3600 \
  --continue-on-failure \
  --no-shrink \
  --suppress-health-check all
```

## Bound the run by the clock

If the comparison fixes a time budget, pass `--max-time` equal to that budget.

By default a run generates at most 100 examples per operation and then exits, which on a small API takes well under a second. Against tools that run until the clock expires, the two are not comparable - one spent the budget and the other did not. Under `--max-time` the fuzzing and stateful phases keep drawing new cases until the budget is spent, and every metric a benchmark reports depends on the requests actually sent.

## Keep testing after the first failure

By default an operation stops as soon as it produces a failure, so most of its budget goes unspent and any further bug in it is never found. On an API where many operations fail early, `--continue-on-failure` is the difference between a handful of requests and the full budget.

## Skip shrinking

Shrinking re-sends variations of an input that already failed, to report the smallest one. That helps a developer debug, but it spends budget narrowing a known failure rather than exploring, so `--no-shrink` returns that time to generation.

Leave shrinking on when the minimized inputs are themselves part of what you report.

## Disable health checks

A health check aborts an operation's test when generation is slow or filters too much, and the operation is then reported as an error rather than tested. In a benchmark that removes an operation from the measurement for reasons that have nothing to do with the API under test, so `--suppress-health-check all` keeps every operation in.

This is the opposite of the advice for a normal run, where a health check is a signal worth reading: it usually points at a schema that is expensive or nearly unsatisfiable to generate from.

## Repeat runs, and report the statistics

Generation is randomized, so a single run per configuration is a coin flip rather than a weak result - rankings taken from one run each routinely reverse when the runs are repeated.

Use at least 5 runs per configuration with different seeds, report the median rather than the mean, and back any ranking with a Mann-Whitney U test and a Vargha-Delaney A12 effect size.

## Report the configuration

Include the Schemathesis version and the exact command line.

"Default configuration" is not reproducible on its own: defaults change between releases, and the default stopping condition is example-based, which interacts with whatever budget the harness imposes.
