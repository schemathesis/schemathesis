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

Writing OpenAPI links manually for every operation relationship can be time-consuming and error-prone. Schemathesis can automatically infer many of these connections by analyzing `Location` headers from API responses during testing.

**How it works:**

1. **Data collection**: During earlier test phases, Schemathesis collects `Location` headers from API responses
2. **Pattern analysis**: It analyzes these headers to understand how your API structures resource locations
3. **Connection mapping**: Creates connections between operations - it learns how to extract parameters from `Location` headers and use them to call related operations
4. **Stateful testing**: Uses this knowledge to chain operations together with real parameter values

**Example:**

**Phase 1 - Learning**: During fuzzing, Schemathesis observes:

```http
POST /users → 201 Created
Location: /users/123
```

From this, it learns these connections:

- `GET /users/{userId}` - can be called using `userId` extracted from `Location`
- `PUT /users/{userId}` - can be called using `userId` extracted from `Location`
- `GET /users/{userId}/posts` - can be called using `userId` extracted from `Location`

**Phase 2 - Application**: During stateful testing, it uses this knowledge:

```http
POST /users → Location: /users/456 → automatically calls GET /users/456, PUT /users/456, etc.
```

This happens automatically when stateful testing is enabled - no additional configuration required.

!!! note "Location Headers Required"
    This feature requires your API to return `Location` headers pointing to created or updated resources.

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
