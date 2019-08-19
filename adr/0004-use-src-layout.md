# 2. Use src layout

Date: 2019-08-12

## Status

Accepted

## Context

We need to have reliable distribution publishing process and always
run tests against installed package version.

## Decision

We will use `src` code layout as
[described by Ionel Christian Mărieș](https://blog.ionelmc.ro/2014/05/25/python-packaging/) and [Hynek Schlawack](https://hynek.me/articles/testing-packaging/).

## Consequences

Chances of publishing invalid/incomplete package are reduced
and the tests are executed against installed package version.
