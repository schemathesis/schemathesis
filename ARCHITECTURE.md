# Architecture

Schemathesis is a property-based API testing framework. It loads an API
description (OpenAPI or GraphQL), discovers its *operations*, generates requests
against them, and checks the responses.

## A run

1. **Load.** A loader parses the schema and discovers its operations.
2. **Plan.** The engine picks a sequence of *phases* and runs them in order.
3. **Generate.** Each phase produces *cases*: concrete requests with positive
   (schema-conforming) or negative (constraint-violating) data.
4. **Send and check.** The engine sends each case and runs the configured
   *checks* against the response.
5. **Report.** The engine emits events that `st run`, `st fuzz`, and the report
   writers consume.

The pytest plugin runs the same generation, hook, and check machinery inside
pytest's own lifecycle. It has no phases and emits no engine events.

## Phases

| Phase | Generates |
|---|---|
| `probing` | Requests that detect server capabilities |
| `schema_analysis` | No cases; raises schema-level warnings for the run |
| `auth_bootstrap` | Sign-up and login requests that obtain credentials when the user supplied none |
| `examples` | Cases from schema-supplied values |
| `coverage` | Cases that walk every constraint in the schema |
| `fuzzing` | Random, Hypothesis-driven cases |
| `stateful` | Multi-step scenarios driven as a state machine |

Probing, schema analysis, and auth bootstrap run first and are internal: they
have no phase configuration and are left out of the JSON report. Auth bootstrap
follows `[auth] auto-signup`. Examples, coverage, fuzzing, and stateful each
turn off with `[phases.<name>] enabled = false`. The engine skips any phase the
specification does not support, and skips stateful when the schema has no
transitions between operations (e.g., OpenAPI links).

## Transports

A configurable transport sends each case to the server: live HTTP via
`requests`, or direct calls into a mounted WSGI or ASGI application. The same
case runs against any of them.

## Events

The engine emits one stream of events, defined in `engine/events.py`:
`EngineStarted`, `PhaseStarted`/`PhaseFinished`, `ScenarioStarted`/`ScenarioFinished`,
`NonFatalError`, `EngineFinished`, and others. Event handlers in `cli/` consume
that stream: terminal output and one handler per report format
(`cli/commands/run/handlers/`), wired in `cli/executor.py`. Handlers call the
format serializers in `reporting/`. Adding a report needs no engine change.

## Layers

Spec-specific logic lives in `specs/`. The engine, case generation, and hook
machinery reach it through `BaseSchema`, the abstract interface in `schemas.py`,
and stay spec-agnostic.
The coverage phase's JSON Schema constraint walker also lives in `specs/`
(`specs/openapi/coverage/`); the
engine calls it through methods on that interface.

Outside `specs/`, these import it at runtime: the public `openapi/` and
`graphql/` packages, `cli/`, and `checks.py`, which imports the OpenAPI checks so
their `@schemathesis.check` decorators register them.
`generation/` has two type-only imports of `specs.openapi`.

| Module | Responsibility |
|---|---|
| `core/` | Foundation: errors, JSON Schema utilities, primitive data types. Depends only on stdlib and third-party packages |
| `schemas.py` | `BaseSchema`, the abstract schema interface, and `APIOperation`, the operation type shared by every spec |
| `openapi/`, `graphql/` | Public entry points: `from_url`, `from_path`, and other loaders, plus the failure types their checks raise |
| `specs/openapi/`, `specs/graphql/` | Concrete schema implementations: parsing, spec-flavored generation, spec-specific check functions (`specs/openapi/checks.py`) |
| `generation/` | Produces cases |
| `hooks.py`, `hook_specs.py` | Hook dispatch, and the name and signature of every hook |
| `checks.py` | The `CHECKS` registry, the `@schemathesis.check` decorator, and the context passed to every check |
| `auths.py` | Custom authentication providers |
| `filters.py` | Operation filters (`include`/`exclude`) |
| `engine/` | The test runner described above. `core.py` builds the phase plan and skips unsupported phases; `run/` executes the phases |
| `transport/` | Sends a case: over HTTP with `requests`, or into a WSGI or ASGI app through the clients in `python/` |
| `cli/` | The `st` command line (`run`, `fuzz`, `replay`) and the event handlers that write reports; JSON and WFC reports live in `cli/json_report.py` and `cli/wfc_report.py` |
| `reporting/` | Serializers for HAR, VCR, JUnit, Allure, NDJSON, and HTML reports; crash files for `st replay` |
| `pytest/` | The pytest plugin |
| `config/` | Parses `schemathesis.toml`; `ReportFormat` lists the report formats |
| `baseline/` | The known-failure baseline file: load, match, save |
| `wfc/` | Web Fuzzing Commons authentication |
| `python/` | In-process ASGI, WSGI, and Django clients, plus the registry behind `@schemathesis.python.constants` |
