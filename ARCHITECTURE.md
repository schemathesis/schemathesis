# Architecture

Schemathesis is a property-based API testing framework. A run starts by loading
an API description (OpenAPI or GraphQL) and discovering its *operations*.

The engine plans a sequence of *phases* (examples, coverage, fuzzing, stateful)
and runs them in order. Each phase generates *cases*: concrete requests with
positive (schema-conforming) or negative (constraint-violating) data.

The engine sends each case, runs the configured *checks* against the response,
and emits events that the `st run` command and the report writers consume.

The pytest plugin is a separate test runner: instead of using the engine, it
plugs the same generation, hook, and check machinery into pytest's own
lifecycle.

The phases differ in how they generate cases: examples uses schema-supplied
values, coverage walks every constraint systematically, fuzzing is random
Hypothesis-driven, and stateful drives multi-step scenarios as a state machine.

Cases reach the server through a configurable transport: live HTTP via
`requests`, or direct calls into a mounted WSGI or ASGI application. Sending is
decoupled from generation; the same case runs against any of these.

Events decouple production from consumption. The engine emits a uniform stream
regardless of what is listening; consumers plug in without the engine knowing
they exist.

## Layers

The engine, case generation, and hook machinery are spec-agnostic: they work
through an abstract schema interface and never depend on a concrete spec.
Spec-specific logic lives in `specs/`.

`core/` is the foundation: transports, errors, JSON Schema utilities, primitive
data types. Depends only on stdlib and third-party packages.

`schemas.py` defines the abstract schema class and the endpoint data type shared
by every spec.

`specs/openapi/` and `specs/graphql/` are the concrete schema implementations:
parsing, spec-flavored generation, spec-specific checks.

`generation/` and `hooks.py` produce cases and dispatch hooks.

`engine/` is the test runner described above.

`cli/` is the `st run` command. `reporting/` writes HAR, VCR, JUnit, and Allure
reports from the engine's event stream. `pytest/` is the pytest plugin, which
runs tests through pytest instead of the engine.
