# Booking API Example

A simple booking API for demonstrating Schemathesis testing capabilities.

## Features

- Create hotel bookings with guest validation
- Retrieve booking information
- Bearer token authentication
- OpenAPI schema generation

## Running the API

```bash
docker compose up -d
```

The API will be available at:

- **Base URL**: http://localhost:8080

- **API Documentation**: http://localhost:8080/docs

- **OpenAPI Schema**: http://localhost:8080/openapi.json

## Authentication

All booking endpoints require bearer token authentication:

```
Authorization: Bearer secret-token
```

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

## Tutorial Progression

This API contains intentional bugs for educational purposes:

1. **Initial state**: Insufficient validation causes 500 errors on edge cases
2. **After first fix**: Added name validation introduces new edge case failures
3. **Final state**: Proper validation handles all edge cases

The bugs demonstrate how Schemathesis catches issues that manual testing typically misses.
