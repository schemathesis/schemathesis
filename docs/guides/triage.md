# Triaging Failures

Running Schemathesis against a real API for the first time often produces more failures than you can address at once. This guide gives you a framework for working through them systematically.

## Get the Full Picture

Start with a plain run:

```bash
uvx schemathesis run https://api.example.com/openapi.json
```

The output summary groups failures by type:

```
❌ Undocumented HTTP status code: 47
❌ Response schema conformance: 12
❌ Server error: 3
```

The order above reflects their typical distribution — and is also the order to address them.

## Undocumented Status Codes

These dominate most first runs because APIs rarely document every possible error response.

**Fix:** Add the missing status codes to your schema. Look for patterns — if every GET operation returns an undocumented `404`, you can add it in one pass across all of them.

If you're not ready to fix these yet, suppress them while focusing on more severe issues:

```bash
uvx schemathesis run https://api.example.com/openapi.json \
  --exclude-checks status_code_conformance
```

## Response Schema Conformance

These mean your API returns responses that don't match the shape declared in your schema — wrong field types, missing required fields, or extra undeclared properties.

Don't try to fix these across the whole API at once. Focus on one area at a time:

```bash
uvx schemathesis run https://api.example.com/openapi.json \
  --include-path /users
```

Or by tag if your schema uses them:

```bash
uvx schemathesis run https://api.example.com/openapi.json \
  --include-tag users
```

Fix one area, verify it passes, then move to the next.

## Server Errors

The fewest in number but highest severity — your API is crashing on generated input.

For each server error, follow the diagnostic process from the [CLI Tutorial](../tutorials/cli.md#first-test-run): run the reproduction `curl`, read the response body, and trace the minimal failing input back to its schema definition. These need individual investigation — there is no batch fix.

## What's Next

Once your run is clean, see [Optimizing for Maximum Bug Detection](config-optimization.md) to run longer, higher-coverage sessions.
