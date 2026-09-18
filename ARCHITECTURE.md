# Architecture

Schemathesis is a property-based API testing framework. It loads an API
description (OpenAPI or GraphQL), discovers its *operations*, generates requests
against them, and checks the responses.

## A run

1. **Load.** The schema is parsed and its operations are discovered.
2. **Plan.** The engine picks a sequence of *phases* and runs them in order.
3. **Generate.** Each phase produces *cases*: concrete requests with positive
   (schema-conforming) or negative (constraint-violating) data.
4. **Send and check.** The engine sends each case and runs the configured
   *checks* against the response.
5. **Report.** The engine emits events that `st run`, `st fuzz`, and the report
   writers consume.

The pytest plugin is a separate test runner: instead of using the engine, it
plugs the same generation, hook, and check machinery into pytest's own
lifecycle.

## Phases

Phases differ in how they generate cases:

| Phase | Generates |
|---|---|
| `probing` | Requests that detect server capabilities |
| `schema_analysis` | No cases; raises schema-level warnings for the run |
| `examples` | Cases from schema-supplied values |
| `coverage` | Cases that walk every constraint systematically |
| `fuzzing` | Random, Hypothesis-driven cases |
| `stateful` | Multi-step scenarios driven as a state machine |

Probing and schema analysis run first and are internal: they have no
configuration of their own and are left out of the JSON report. The other four
can be disabled through configuration. Any phase is skipped when the
specification does not support it, and stateful is skipped when the schema
declares no transitions.

## Transports

Cases reach the server through a configurable transport: live HTTP via
`requests`, or direct calls into a mounted WSGI or ASGI application. Sending is
decoupled from generation; the same case runs against any of these.

## Events

Events decouple production from consumption. The engine emits a uniform stream
regardless of what is listening; consumers plug in without the engine knowing
they exist.

## Layers

Spec-specific logic lives in `specs/`. The engine, case generation, and hook
machinery reach it through an abstract schema interface and are meant to stay
spec-agnostic; a handful of imports from `specs/openapi` and `specs/graphql`
still cross that line and are being worked out.

| Module | Responsibility |
|---|---|
| `core/` | Foundation: errors, JSON Schema utilities, primitive data types. Depends only on stdlib and third-party packages |
| `schemas.py` | The abstract schema class and the endpoint data type shared by every spec |
| `specs/openapi/`, `specs/graphql/` | Concrete schema implementations: parsing, spec-flavored generation, spec-specific checks |
| `generation/`, `hooks.py` | Produce cases and dispatch hooks |
| `engine/` | The test runner described above |
| `transport/` | The concrete senders: `requests`, WSGI, ASGI |
| `cli/` | The `st` command line: `run`, `fuzz`, and `replay` |
| `reporting/` | HAR, VCR, JUnit, Allure, and NDJSON reports from the engine's event stream |
| `pytest/` | The pytest plugin, which runs tests through pytest instead of the engine |
| `config/` | Parses `schemathesis.toml` |
| `baseline/` | The known-failure baseline file: load, match, save |
| `wfc/` | Web Fuzzing Commons authentication |
| `python/` | In-process calls into ASGI, WSGI, and Django apps, plus the registry behind `@schemathesis.python.constants` |

Phase-level case enumeration that walks spec-specific constructs (e.g., the
coverage phase's JSON Schema constraint walker) lives in `specs/` too; the
engine reaches it through methods on the abstract schema interface rather than
by importing `specs/`.
