# Understanding Stateful Testing

Learn how Schemathesis's stateful testing works, when to use it, and how it fits into your testing strategy.

## What is Stateful Testing?

Stateful testing chains API calls together using real data from responses, rather than testing each operation independently.

**Without stateful testing:**

```
POST /users → Creates user → Test passes ✓
GET /users/123 → Uses random ID → 404 Not Found ✗
DELETE /users/456 → Uses random ID → 404 Not Found ✗
```

Each operation tested alone. Random data doesn't match real resources.

**With stateful testing:**

```
POST /users → Creates user → Returns ID: 789 ✓
GET /users/789 → Uses actual ID from POST → 200 OK ✓
DELETE /users/789 → Uses actual ID from POST → 200 OK ✓
```

Operations chained together. Real IDs from real responses.

## How It Works

Schemathesis analyzes your OpenAPI schema to understand how operations connect.

**Step 1: Find resources**

Identifies what your API produces:

```yaml
POST /users → Creates "User" resource
POST /orders → Creates "Order" resource
```

**Step 2: Match parameters**

Links parameters to resources:

```yaml
GET /users/{userId} → Needs "userId" from User resource
GET /orders/{orderId} → Needs "orderId" from Order resource
```

**Step 3: Connect operations**

Chains operations that share resources:
```
POST /users (creates User with id=123)
  ↓ pass id as userId
GET /users/{userId} (needs User)
  ↓ pass id as userId
PUT /users/{userId} (needs User)
  ↓ pass id as userId
DELETE /users/{userId} (needs User)
```

**Step 4: Generate test scenarios**

Runs random workflows:

- Create user -> Get user -> Update user -> Delete user
- Create user -> Create order for that user -> Get order
- Create user -> Delete user -> Get user

## Reusing Response Data in Non-Stateful Testing

When fuzzing `GET /users/{id}` with random IDs, nearly every request returns 404. Error handling gets thoroughly tested, but success logic — response schema validation, data serialization, permission checks—remains largely untouched because valid IDs are astronomically rare in random generation.

Schemathesis captures useful values from successful responses and reuses them when generating test cases. Dependency analysis identifies which operations produce resources and which consume them. For example, it recognizes that `POST /users` creates users with IDs, and `GET /users/{id}` needs those IDs.

During fuzzing, captured values augment random generation. `GET /users/{id}` tests with both random IDs (finding 404 handling bugs) and real IDs from earlier `POST /users` calls (finding bugs in success paths).

This works across non-stateful test phases and within them. The examples phase might create a user that the fuzzing phase later references. Within fuzzing itself, early test cases discover values that later cases use.

## Connecting Operations

Stateful testing needs to know how operations relate. For example, `POST /users` creates a user, and `GET /users/{userId}` needs that user's ID.

Schemathesis discovers these connections in three ways:

### 1. Automatic Schema Analysis

Analyzes your OpenAPI schema to detect connections.

**Example:** Your schema has:

```yaml
paths:
  /users:
    post:
      responses:
        '201':
          content:
            application/json:
              schema:
                properties:
                  id: {type: string}
                  email: {type: string}
  
  /users/{userId}:
    get:
      parameters:
        - name: userId
          in: path
```

Schemathesis detects the following relationships:

- `POST /users` creates a `User` resource with fields `id` and `email`.
- `GET /users/{userId}` requires a `userId` path parameter.
- It infers that `userId` corresponds to the `id` field returned by the POST response.
- Therefore, it can build a sequence: `POST /users` -> `GET /users/{userId}`.

**Works for:**

- Path parameters: `userId`, `user_id`, `{id}` in `/users/{id}`
- Nested resources: `/users/{userId}/posts`
- Pagination: `{"data": [...]}`, `{"items": [...]}`
- Schema composition: `allOf`, `oneOf`, `anyOf`

### 2. Location Header Learning

While running tests, Schemathesis can also learn new connections dynamically by observing `Location` headers in responses.

If your API returns a `Location` header when creating resources, Schemathesis automatically discovers follow-up operations for that resource.

```http
POST /users → 201 Created
Location: /users/123

# Learns: GET /users/123, PUT /users/123, DELETE /users/123
```

This mechanism requires your API to return a valid `Location` header in `201 Created` responses.

### 3. Manual OpenAPI Links

When you want full control or need to specify non-path relationships, you can define explicit connections using [OpenAPI Links](https://spec.openapis.org/oas/v3.1.0#link-object).

```yaml
paths:
  /users:
    post:
      responses:
        '201':
          links:
            GetUser:
              operationId: getUser
              parameters:
                userId: '$response.body#/id'
```

This explicitly tells Schemathesis that the `userId` parameter in the `getUser` operation should be populated from the `id` field in the response body of the `POST /users` operation.

**Use manual links when**:

- Automatic schema analysis misses a connection
- You want precise, explicit control over operation relationships

!!! note "All Three Work Together"
    Schema analysis runs first, manual links override when present, and `Location` learning adds runtime discoveries.

## How Schemathesis Extends OpenAPI Links

### Regex Extraction from Headers and Query Parameters

Standard OpenAPI links can extract string data from various places, but only exact values. Schemathesis adds regex support for pattern-based extraction for a part of a string:

```yaml
paths:
  /users:
    post:
      responses:
        '201':
          headers:
            Location:
              schema:
                type: string
          links:
            GetUserByUserId:
              operationId: getUser
              parameters:
                userId: '$response.header.Location#regex:/users/(.+)'
```

**How it works:**

- If `Location` header is `/users/42`, the `userId` parameter becomes `42`
- The regex must be valid Python regex with exactly one capturing group
- If regex doesn't match, the parameter is set to empty string

### Enhanced RequestBody Support

OpenAPI standard does not allow recursive expressions in `requestBody`:

```yaml
SetManagerId:
  operationId: setUserManager
  # only plain or embedded expression or literals
  requestBody: "$response.body#/id"
```

Schemathesis allows for nested expressions:

```json

SetManagerId:
  operationId: setUserManager
  requestBody: {
    "user_id": "$response.body#/id",
    "metadata": {
      "created_by": "$response.body#/author",
      "tags": ["$response.body#/category", "static-value"]
    }
  }
```

If response body is `{"id": 123, "author": "alice", "category": "blog"}`, the request body becomes:

```json
{
  "user_id": 123,
  "metadata": {
    "created_by": "alice",
    "tags": ["blog", "static-value"]
  }
}
```

### Backwards Compatibility

**OpenAPI 2.0 Support:** Use `x-links` extension with identical syntax:
```yaml
# OpenAPI 3.0
links:
  GetUser: ...

# OpenAPI 2.0
x-links:
  GetUser: ...  # Same syntax, including regex support
```
