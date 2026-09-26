# Booking API Example

A sample booking API that demonstrates how Schemathesis automatically discovers various types of bugs that manual testing typically misses.

> 📚 **Tutorials available:** This API is used in the **[CLI Tutorial](../../docs/tutorials/cli.md)** (20 minutes) and the **[Pytest Tutorial](../../docs/tutorials/pytest.md)** (15 minutes).

## Overview

- Hotel booking creation and retrieval
- Bearer token authentication
- Room type validation with an intentional bug
- Open API 3.1 schema provided by FastAPI

## Quick Start

Start the API:
```bash
docker compose up -d
```

Verify it's running:

Open [http://localhost:8080/docs](http://localhost:8080/docs) to see the interactive API documentation.

The API will be available at:

- **Base URL**: http://localhost:8080

- **OpenAPI Schema**: http://localhost:8080/openapi.json

## Authentication

All booking endpoints require bearer token authentication.

```bash
Authorization: Bearer secret-token
```

## Test with Schemathesis

```bash
uvx schemathesis run http://localhost:8080/openapi.json \
  --header 'Authorization: Bearer secret-token'
```

Schemathesis will discover an edge case that causes a 500 error.

## Example Usage

Create a booking:
```bash
curl -X POST "http://localhost:8080/bookings" \
  -H "Authorization: Bearer secret-token" \
  -H "Content-Type: application/json" \
  -d '{
    "guest_name": "John Doe",
    "room_type": "deluxe",
    "nights": 3
  }'
```

Retrieve booking:
```bash
curl -H "Authorization: Bearer secret-token" \
  http://localhost:8080/bookings/42
```
