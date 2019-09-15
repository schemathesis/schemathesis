# 6. Resolve references & load schemas eagerly

Date: 2019-09-15

## Status

Accepted

## Context

Having lazily loaded schema complicates implementation and doesn't bring much value, since loading still happen during the collection phase.
References are not implemented lazily at the moment, it was missed during development.

## Decision

Evaluate schemas & references eagerly.

## Consequences

Implementation will be simpler. However, references still could be implemented lazily,
since they could be evaluated after collection phase and the tests using them could be excluded from the run.
