# Checks

Schemathesis automatically validates API responses using various checks. Each check targets specific aspects of API behavior and its compliance to the specification.

## Core Response Validation

These checks validate that API responses conform to your API schema.

### `not_a_server_error`

**Detects server-side errors and implementation issues**

**Triggers on:** 5xx HTTP status codes or GraphQL `errors` field

### `status_code_conformance`
**Verifies response status codes match schema documentation**

**Triggers on:** Status codes not documented in schema

### `content_type_conformance`
**Validates `Content-Type` header matches schema**

**Triggers on:** Content types not matching schema-documented media types  

### `response_headers_conformance`
**Ensures required response headers are present and valid**

**Triggers on:** Missing or invalid required headers

### `response_schema_conformance`
**Validates response body against JSON Schema**

**Triggers on:** Response body not matching schema structure

## Input Handling Validation

These checks verify how your API processes different types of request data.

### `negative_data_rejection`
**Verifies API rejects invalid request data with appropriate errors**

**Triggers on:** API accepting schema-violating requests

### `positive_data_acceptance`
**Ensures API accepts valid request data**

**Triggers on:** API rejecting schema-compliant requests  

### `missing_required_header`
**Checks APIs return 4xx for missing required headers**

**Triggers on:** API not rejecting requests missing required headers

### `unsupported_method`
**Verifies APIs return 405 for undocumented HTTP methods**

**Triggers on:** Non-405 responses for undocumented methods on valid paths

---

## Stateful Behavior

These checks test API behavior across sequences of operations.

### `use_after_free`
**Detects when deleted resources remain accessible**

**Triggers on:** Accessing deleted resources doesn't return 404

### `ensure_resource_availability`
**Verifies created resources are immediately accessible**

**Triggers on:** Newly created resources can't be retrieved/modified

## Security

### `ignored_auth` <small>*authentication*</small>
**Tests whether authentication requirements are enforced**

**Triggers on:** Protected endpoints accepting requests without proper auth
