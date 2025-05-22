# GitLab CI Integration

This page shows how to run Schemathesis tests in GitLab CI/CD pipelines.

## Basic Configuration

Add this configuration to your `.gitlab-ci.yml` file:

```yaml
api-tests:
  stage: test
  image:
    name: schemathesis/schemathesis:stable
    entrypoint: [""]
  script:
    # Set up application here if needed
    - st run http://127.0.0.1:5000/api/openapi.json
```
