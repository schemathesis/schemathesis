# Checks

Schemathesis automatically validates API responses using various checks. Each check targets specific aspects of API behavior and its compliance to the specification.

## Core Response Validation

These checks validate that API responses conform to your API schema.

### `not_a_server_error`

Catches server-side errors and implementation issues

**Examples** 

- `500 Internal Server Error`
- `502 Bad Gateway` 
- GraphQL response with `"errors": [{...}]`

---

### `status_code_conformance`

Verifies response status codes match schema documentation

**Examples**

- Schema documents `200, 404` but API returns `403`
- Schema documents `201` but API returns `200`

---

### `content_type_conformance`

Validates `Content-Type` header matches schema

**Examples**

- Schema specifies `application/json` but API returns `text/html`
- Schema specifies `application/xml` but API returns `application/json`

---

### `response_headers_conformance`

Ensures required response headers are present and valid

**Example**

Schema specifies response must include `X-RateLimit-Remaining` header:

```http
GET /api/data

Response: 200 OK
Content-Type: application/json
# Missing required X-RateLimit-Remaining header ❌

{"data": [...]}
```

---

### `response_schema_conformance`

Validates response body against JSON Schema

**Example**

Schema defines user object as:

```yaml
type: object
required: [id, name, email]
properties:
  id: {type: integer}
  name: {type: string}
  email: {type: string, format: email}
```

But API returns:

```http
GET /users/789

Response: 200 OK
{
  "id": "789",          ❌ Should be integer, not string
  "name": "Eve"         ❌ Missing required 'email' field
}
```

## Input Handling Validation

These checks verify how your API processes different types of request data.

### `negative_data_rejection`

Verifies API rejects invalid data with appropriate error responses

**Example**

Schema specifies `age` must be an integer:

```http
POST /users
Content-Type: application/json

{"name": "Charlie", "age": "twenty-five"}

Response: 200 OK  ❌ Should return 400 Bad Request
{"id": 456, "name": "Charlie", "age": null}
```

---

### `positive_data_acceptance`

Verifies API accepts valid request data

**Example**

Request perfectly matches schema:

```http
POST /users
Content-Type: application/json

{"name": "Diana", "age": 25, "email": "diana@example.com"}

Response: 400 Bad Request  ❌ Should accept valid data
{"error": "Invalid request"}
```

---

### `missing_required_header`

Verifies APIs return 4xx for missing required headers

**Example**

Schema requires `X-API-Key` header, but:

```http
GET /protected-resource
# Missing required X-API-Key header

Response: 200 OK  ❌ Should return 400 Bad Request or 401 Unauthorized
```

---

### `unsupported_method`

Verifies APIs return 405 for undocumented HTTP methods

**Example**

Schema only documents `GET /users/{id}` and `PUT /users/{id}`, but:

```http
PATCH /users/123
Content-Type: application/json

{"name": "Bob"}

Response: 200 OK  ❌ Should return 405 Method Not Allowed
```

## Stateful Behavior

These checks test API behavior across sequences of operations.

### `use_after_free`

Detects when deleted resources remain accessible

**Example**

```http
DELETE /users/123

Response: 204 No Content
```

Then:

```http
GET /users/123

Response: 200 OK  ❌ Should return 404 Not Found
```

---

### `ensure_resource_availability`

Verifies created resources are immediately accessible

**Example**

```http
POST /users
Content-Type: application/json

{"name": "Alice", "email": "alice@example.com"}

Response: 201 Created
Location: /users/123
```

Then immediately:

```http
GET /users/123

Response: 404 Not Found  ❌ Should return the created user
```

## Security

### `ignored_auth`

Verifies authentication is properly enforced

**Example**

- Endpoint requires authentication but accepts requests without auth headers
- Returns `200 OK` instead of `401 Unauthorized`
