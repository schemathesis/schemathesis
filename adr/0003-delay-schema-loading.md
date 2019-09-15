# 4. Delay schema loading

Date: 2019-08-08

## Status

Superseded by #6

## Context

We need to have minimal performance overhead on a test collection
and avoid loading that involves network requests.

## Decision

We will load schema during the test execution.

## Consequences

Test collection phase becomes faster, also it allows to avoid
unnecessary schema loading over the wire.
