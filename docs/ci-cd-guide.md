# Continuous Integration

This guide outlines how to set up Schemathesis for automated API testing in your Continuous Integration workflows.

## Preparing Your Application

Before integrating Schemathesis into your CI/CD pipeline, you'll need to ensure your application is properly set up for testing:

```yaml
# Example GitHub Actions workflow for a Python app
api-tests:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v3.0.0
    - uses: actions/setup-python@v4
      with:
        python-version: '3.10'
    - run: pip install -r requirements.txt
    # Start the API in the background
    - run: python main.py &
    # Wait for the application to start
    - run: sleep 5
    - name: Run Schemathesis tests
      uses: schemathesis/action@v1
      with:
        schema: 'http://127.0.0.1:5000/api/openapi.json'
```

## Working with API Schemas

### URL-Based Schemas

If your API provides the schema at a URL endpoint:

```yaml
# Example configuration for URL-based schema
- name: Run Schemathesis tests
  uses: schemathesis/action@v1
  with:
    schema: 'http://127.0.0.1:5000/api/openapi.json'
```

### File-Based Schemas

If your API schema is maintained in a file separate from the application:

```yaml
# Example configuration for file-based schema
- name: Run Schemathesis tests
  uses: schemathesis/action@v1
  with:
    schema: './docs/openapi.json'
    base-url: 'http://127.0.0.1:5000/api/v2/'
```

## CI Platform Integration

### GitHub Actions

For integration with GitHub Actions:

```yaml
api-tests:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v3.0.0
    # Set up application here
    - name: Run Schemathesis tests
      uses: schemathesis/action@v1
      with:
        schema: 'http://127.0.0.1:5000/api/openapi.json'
```

#### Adding Custom Headers

For authentication or other custom requirements:

```yaml
# Save access token to $GITHUB_ENV as ACCESS_TOKEN
- name: Set access token
  run: echo "ACCESS_TOKEN=super-secret" >> $GITHUB_ENV

- name: Run Schemathesis tests
  uses: schemathesis/action@v1
  with:
    schema: 'http://example.com/api/openapi.json'
    args: '-H "Authorization: Bearer ${{ env.ACCESS_TOKEN }}"'
```

### GitLab CI

For GitLab CI pipelines:

```yaml
api-tests:
  stage: test
  image:
    name: schemathesis/schemathesis:stable
    entrypoint: [""]
  script:
    # Set up application here if needed
    - st run http://127.0.0.1:5000/api/openapi.json --checks=all
```

## Configuration Options

### Environment Variables

You can configure Schemathesis behavior using these environment variables:

- **SCHEMATHESIS_HOOKS**: Points to a Python module with user-defined Schemathesis extensions.
  Example: `my_module.my_hooks`

- **SCHEMATHESIS_BASE_URL**: Set when using a file-based schema to specify the API's base URL.
  Example: `http://127.0.0.1:5000/api/v2/`

- **SCHEMATHESIS_WAIT_FOR_SCHEMA**: Time in seconds to wait for the schema to be accessible.
  Example: `10`

### Common CLI Arguments

Frequently used CLI arguments in CI environments:

- `--checks=all`: Run all available checks
- `--max-failures=N`: Stop after N failures to prevent excessive test runs
