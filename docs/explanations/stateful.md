# Understanding Stateful Testing

Why does fuzzing `GET /users/{userId}` rarely get past `404 Not Found`, and what does Schemathesis do about it? This page explains how the stateful phase chains operations with real response data, where the connections between operations come from, and what a stateful run does to your API.

## What is Stateful Testing?

Stateful testing chains API calls together using real data from responses, rather than testing each operation independently.

Without stateful testing, each operation is tested alone and random data rarely matches a real resource:

```
POST /users -> Creates user -> Test passes ✓
GET /users/123 -> Uses random ID -> 404 Not Found ✗
DELETE /users/456 -> Uses random ID -> 404 Not Found ✗
```

With stateful testing, operations are chained and use real IDs from real responses:

```
POST /users -> Creates user -> Returns ID: 789 ✓
GET /users/789 -> Uses actual ID from POST -> 200 OK ✓
DELETE /users/789 -> Uses actual ID from POST -> 200 OK ✓
```

Single-operation phases (examples, coverage, fuzzing) find input-handling bugs in one request. The stateful phase reaches bugs that need a sequence: reading a resource after it was updated, using it after it was deleted, or creating one resource from another. In a default CLI run it executes after the other phases, whenever Schemathesis knows at least one connection between operations.

## How It Works

Schemathesis analyzes your schema to understand how operations connect. The mechanism differs by spec but the model is the same: identify producers (operations that return resources), identify consumers (operations that need resources), and chain them.

It first identifies what your API produces and which parameters need those resources:

```text
POST /users -> Creates "User" resource
POST /orders -> Creates "Order" resource

GET /users/{userId} -> Needs "userId" from User resource
GET /orders/{orderId} -> Needs "orderId" from Order resource
```

Operations that share a resource are then connected:

```text
POST /users (creates User with id=123)
  -> pass id as userId
GET /users/{userId} (needs User)
  -> pass id as userId
PUT /users/{userId} (needs User)
  -> pass id as userId
DELETE /users/{userId} (needs User)
```

From these connections, Schemathesis generates random workflows such as:

- Create user -> Get user -> Update user -> Delete user
- Create user -> Create order for that user -> Get order
- Create user -> Delete user -> Get user

## Connecting Operations

Schemathesis discovers connections per spec:

- **OpenAPI**: schema analysis, `Location` headers, and explicit OpenAPI Links.
- **GraphQL**: the type graph itself encodes the connections — a mutation returning `Book!` is automatically connected to a query taking a `Book` id-typed argument.

### 1. Automatic Schema Analysis (OpenAPI)

Schemathesis analyzes your OpenAPI schema to detect connections. Given this schema:

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

The analysis handles:

- Path parameters: `userId`, `user_id`, `{id}` in `/users/{id}`
- Nested resources: `/users/{userId}/posts`
- Pagination: `{"data": [...]}`, `{"items": [...]}`
- Schema composition: `allOf`, `oneOf`, `anyOf`

### 2. Automatic Schema Analysis (GraphQL)

For GraphQL, Schemathesis reads the type graph directly. Object types with an `id` field become resources; mutations returning those types become producers; queries and mutations whose id-typed arguments resolve to those types become consumers.

```graphql
type Book { id: ID! title: String! }

type Mutation {
    addBook(title: String!): Book!     # Producer of Book
    deleteBook(id: ID!): Boolean       # Cleanup of Book
}

type Query {
    book(id: ID!): Book                # Consumer of Book
}
```

Schemathesis builds the chain `addBook -> book` (and `addBook -> deleteBook`) automatically.

Argument-name conventions are recognized: `bookId`, `book_id`, `bookIds` all resolve to the `Book` type. Bespoke `<Type>ID` scalars (e.g. `BookID`) work the same way.

### 3. Location Header Learning

While running tests, Schemathesis can also learn connections by observing `Location` headers in responses. When a response carries a `Location` header that points to another operation in the schema, Schemathesis adds a link to that operation under the status code the response had.

```http
POST /users -> 201 Created
Location: /users/123

# Learns: GET /users/123, PUT /users/123, DELETE /users/123
```

The headers are collected while the CLI runs the examples, coverage, and fuzzing phases, and the learned links are used by the stateful phase that follows. If you disable those phases, or run the stateful tests from Python or pytest, nothing is learned this way.

### 4. Manual OpenAPI Links

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

Use manual links when automatic schema analysis misses a connection, or when you want precise, explicit control over operation relationships.

### How the Sources Combine

All sources add links; none of them disables another.

- Links from schema analysis are added next to your manual links. An inferred link is skipped when it targets the same operation as an existing link and its parameters and request body are a subset of that link's. If its name is already taken, it gets a unique name.
- Links learned from `Location` headers are added regardless of manual links. Duplicates among the learned links are skipped.

## How Schemathesis Extends OpenAPI Links

### Regex Extraction

Standard OpenAPI link expressions take a whole value. Schemathesis lets you extract part of a string value with a regular expression, for response headers (`$response.header.<name>`) and for the path, query, and header parameters of the request that was sent (`$request.path.<name>`, `$request.query.<name>`, `$request.header.<name>`):

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

If the `Location` header is `/users/42`, the `userId` parameter becomes `42`. The rules:

- The pattern is a Python regular expression with exactly one capturing group; any other number of groups makes the link expression invalid.
- The pattern is searched anywhere in the value, not matched against the whole of it, and the captured group becomes the parameter value.
- If the pattern does not match, or the group captures an empty string, the link does not set that parameter, and Schemathesis generates a value for it as usual.

### Enhanced RequestBody Support

The OpenAPI standard does not allow nested expressions in `requestBody`:

```yaml
SetManagerId:
  operationId: setUserManager
  # only plain or embedded expression or literals
  requestBody: "$response.body#/id"
```

Schemathesis allows for nested expressions:

```yaml
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

### OpenAPI 2.0

Use the `x-links` extension with identical syntax:

```yaml
# OpenAPI 3.0
links:
  GetUser: ...

# OpenAPI 2.0
x-links:
  GetUser: ...  # Same syntax, including regex support
```

## What to Expect From a Stateful Run

The stateful phase sends real requests, so it really creates, updates, and deletes data on the target API. Run it against a disposable environment, not against data you need to keep.

Links do not fix every value. For each step, Schemathesis applies a link's value with some probability and otherwise keeps the generated value, so that the chain also tests what happens with an ID that does not exist. Random IDs next to real ones in a stateful run are expected, not a sign that a link is broken.

## Response Data Outside the Stateful Phase

Values captured from responses are not limited to stateful chains. The examples, coverage, and fuzzing phases also draw real IDs from a shared resource pool, so `GET /users/{id}` reaches its success path even without a link. See [how the resource pool works](adaptive-testing.md#reusing-response-data-across-operations).
