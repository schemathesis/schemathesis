# Understanding Stateful Testing

Learn how Schemathesis's stateful testing works, when to use it, and how it fits into your testing strategy.

## What is Stateful Testing?

Stateful testing chains real API calls, passing data from response to request, rather than testing operations in isolation.

**Isolated testing:**
```
POST /users → Random data → Validate response ✓
GET /users/123 → Random ID → 404 Not Found ✗
PUT /users/456 → Random ID → 404 Not Found ✗
```

**Stateful testing:**
```
POST /users → Random data → Returns user ID: 789
GET /users/789 → Use ID from step 1 → 200 OK ✓ 
PUT /users/789 → Use ID from step 1 → 200 OK ✓
```

## How It Works

Schemathesis models your API as a state machine:

- **States** track what data is available from previous calls
- **Transitions** are your API operations  
- **Links** define how data flows between operations

**Process:**

1. Parse OpenAPI links from schema
2. Generate state machine with bundles for each operation
3. Run scenarios: choose operation -> use linked data -> execute -> store response

**Example:** User management API

- Empty state -> `POST /users` -> User state (has user ID)
- User state -> `POST /orders` -> User+Order state (has both IDs)

!!! tip "Under the Hood: Swarm Testing"

    Schemathesis's stateful testing implements [Swarm testing](https://www.cs.utah.edu/~regehr/papers/swarm12.pdf), which makes defect discovery much more effective.

## OpenAPI Links: The Connection Challenge

Stateful testing requires [OpenAPI links](https://swagger.io/docs/specification/links/) to connect operations, but maintaining these links is the biggest implementation challenge.

### What Links Look Like

```yaml
paths:
  /users:
    post:
      responses:
        '201':
          # ... response definition
          links:
            GetUserByUserId:
              operationId: getUser
              parameters:
                userId: '$response.body#/id'  # Use response ID as parameter
  
  /users/{userId}:
    get:
      operationId: getUser
      parameters:
        - name: userId
          in: path
          required: true
```

**Common link patterns:**

- Create resource → Get resource → Update resource → Delete resource
- Create user → Create user's orders → Get order details
- Create parent → Create children → Get parent with children

### Automatic Link Inference

Writing OpenAPI links manually for every operation relationship can be time-consuming and error-prone. Schemathesis can automatically infer many of these connections by analyzing `Location` headers from API responses.

When your API returns a `Location` header (typically after creating or updating resources), it reveals parameter values that can be used to test other operations on the same resource. Schemathesis extracts these parameters and generates links automatically.

**How inference works:**

1. **Exact matching**: Find the endpoint that exactly matches the Location path
2. **Prefix matching**: Find related endpoints that can use the same extracted parameters  
3. **Link generation**: Create links for all matching operations automatically

**Example:**

```
POST /users → 201 Created, Location: /users/123

Automatically inferred links:
- GET /users/{userId}       -> Direct access to created resource
- PUT /users/{userId}       -> Update the created resource
- DELETE /users/{userId}    -> Delete the created resource
- GET /users/{userId}/posts -> Access user's related resources
```

All operations automatically receive `userId: "123"` extracted from the Location header.

- **Reduces manual work**: Automatically discovers many common API relationships
- **Complements manual links**: Works alongside your existing link definitions (manual links take precedence)
- **Uses real values**: Extracts actual IDs and parameters from your API responses
- **Adapts automatically**: Discovers new relationships as your API schema evolves

!!! important "Prerequisites"
    This feature requires your API to return `Location` headers. Not all frameworks include these headers by default, but they're considered a REST best practice for resource creation operations.

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
