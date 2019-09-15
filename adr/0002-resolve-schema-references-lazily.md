# 3. Resolve schema references lazily

Date: 2019-08-08

## Status

Superseded by #6

## Context

We need to have minimal performance overhead on a test collection
and avoid resolving that involves network requests.

## Decision

We will resolve schema references during the test execution.

## Consequences

Test collection phase becomes faster, also it allows to avoid
unnecessary schema resolving if certain tests are de-selected.
