# Booking API Example

A sample booking API that demonstrates how Schemathesis automatically discovers validation bugs that manual testing typically misses.

> **Tutorial available:** This API is used in the **[Schemathesis Tutorial](../../docs/tutorial.md)** - a complete 15-20 minute walkthrough of property-based API testing.

## Features

- Hotel booking creation and retrieval
- Bearer token authentication  
- Room type validation with intentional edge cases
- OpenAPI 3.1 schema with FastAPI

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

Find bugs automatically:
```bash
uvx schemathesis run http://localhost:8080/openapi.json \
  --header 'Authorization: Bearer secret-token'
```

Schemathesis will discover validation edge cases that cause 500 errors.

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
  http://localhost:8080/bookings/{booking_id}
```
---

**📖 Follow the [tutorial](../../docs/tutorial.md) to learn the complete testing workflow!**
