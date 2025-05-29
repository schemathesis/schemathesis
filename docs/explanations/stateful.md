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
