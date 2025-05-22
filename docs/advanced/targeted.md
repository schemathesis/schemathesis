# Targeted Property-Based Testing

Schemathesis supports targeted property-based testing to guide test data generation toward specific goals. This technique helps find problematic inputs faster by directing the test generator toward areas more likely to reveal issues.

## Maximizing Metrics

The `--generation-maximize` option instructs Schemathesis to favor inputs that maximize a specific metric:

```console
$ st run openapi.yaml --generation-maximize response_time
```

Currently supported metrics:

- `response_time`: Favors inputs that result in longer API response times

This approach is particularly useful for detecting:

- Performance bottlenecks
- Inputs that cause excessive processing
- Potential denial-of-service vulnerabilities

!!! info "Custom Metrics"

    See [Extending Metrics](../guides/extending.md) for details on implementing custom metrics for targeted testing.

## Example Scenario

Consider an API endpoint with a hidden performance issue where inputs containing many zeros cause progressively slower responses. At a certain threshold, the endpoint fails completely:

```python
async def performance(request):
    decoded = await request.json()
    number = str(decoded).count("0")
    if number > 0:
        # Progressively slower as zeros increase
        await asyncio.sleep(0.01 * number)
    if number > 10:
        # Fails with too many zeros
        raise ServerError()
    return {"result": "success"}
```

When testing this endpoint:

- **Standard testing** might need many examples to stumble upon problematic inputs
- **Targeted testing** will progressively favor inputs with more zeros, finding the issue faster

## Performance Advantage

In practice, targeted testing can significantly reduce the time needed to discover issues:

```console
# Standard testing might require many examples
$ st run openapi.yaml --max-examples=10000

# Targeted testing often finds issues with fewer examples
$ st run openapi.yaml --max-examples=10000 --generation-maximize response_time
```

While results vary due to the random nature of property-based testing, targeted testing consistently improves efficiency on APIs with performance-related vulnerabilities.
