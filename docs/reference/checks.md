# Checks

Schemathesis runs different kinds of checks against individual API responses and whole sequences of API responses in stateful testing.

## `not_a_server_error`

It checks for server-side errors, covering all 5xx responses. For GraphQL it also checks the `errors` key in the response.

```
- Server error

[500] Internal Server Error:

    `500 Internal Server Error

    Server got itself in trouble`
```

## `status_code_conformance`

Verify if the API response status code is documented in the API schema.

```

```

## `content_type_conformance`

Verify if the API response `Content-Type` header is documented in the API schema.

## `response_headers_conformance`

Verify that all headers documented for this API response are present and conform to the API schema.

## `response_schema_conformance`

Verify whether API response body conforms to the API schema.

## `negative_data_rejection`

Checks whether the API rejects test data that does not match the API schema.

## `positive_data_acceptance`

Checks whether the API accepts test data that matches the API schema.

## `missing_required_header`

Checks whether the API returns 4xx response if the request does not have a required header.

## `unsupported_method`

Checks whether the API returns 405 with the appropriate headers for requests with known paths but undocumented HTTP methods.

## `use_after_free`

Check if a resource is available after being removed.

## `ensure_resource_availability`

Check whether resource is available for retrieval / modification / deletion after being created.

## `ignored_auth`

Check whether a protected API operation actually uses the declared auth by sending requests without auth or with random auth.
