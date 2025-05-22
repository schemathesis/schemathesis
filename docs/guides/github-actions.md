# Using Schemathesis with GitHub Actions

This tutorial demonstrates how to integrate Schemathesis with GitHub Actions for automated API testing. The implementation uses a demo Flask application with intentional errors to showcase Schemathesis's capabilities in a CI environment.

## Prerequisites

- Basic understanding of Docker and Python
- GitHub account
- Familiarity with GitHub Actions

## Setting Up the Demo Environment

The [demo repository](https://github.com/schemathesis/schemathesis-demo) contains a Flask application with several endpoints designed to trigger different types of API errors.

```python
@app.route("/improper-unicode-encoding", methods=["POST"])
def improper_unicode_encoding():
    data = request.json
    if "text" not in data:
        return jsonify({"success": False, "error": "Missing text"}), 400

    try:
        # Simulating improper Unicode handling
        data["text"].encode("ascii")
        return jsonify({"success": True})
    except UnicodeEncodeError:
        return jsonify({"success": False, "error": "Unicode error"}), 500
```

1. Fork the [demo repository](https://github.com/schemathesis/schemathesis-demo) to your GitHub account

2. Clone your fork locally:

   ```console
   $ git clone https://github.com/YOUR-USERNAME/schemathesis-demo.git
   $ cd schemathesis-demo
   ```

3. Start the application:

   ```console
   $ docker compose up -d
   ```

   The API UI will be available at `http://127.0.0.1:5123/ui/`

## Implementing the GitHub Action

The [Schemathesis GitHub Action](https://github.com/schemathesis/action) integrates API testing directly into your CI workflow.

1. Create a new branch for the integration:

   ```console
   $ git checkout -b add-schemathesis-action
   ```

2. Create a workflow file at `.github/workflows/schemathesis.yml`:

   ```yaml
   name: Schemathesis Test

   on: [pull_request]

   jobs:
     test:
       runs-on: ubuntu-latest
       steps:
       - uses: actions/checkout@v4

       - name: Start containers
         run: docker compose up -d --build

       - uses: schemathesis/action@v1
         with:
           schema: 'http://127.0.0.1:5123/openapi.json'

       - name: Stop containers
         if: always()
         run: docker-compose down
   ```

3. Commit and push your changes:

   ```console
   $ git add .github/workflows/schemathesis.yml
   $ git commit -m "Add Schemathesis GitHub Action"
   $ git push -u origin add-schemathesis-action
   ```

4. Open a pull request against your repository

!!! note
    When opening the pull request, select your own repository as the base to ensure the workflow runs in your fork.

The workflow will run when the PR is created, and Schemathesis will identify API issues:

```
________ POST /internal-server-errors/improper-unicode-encoding ________
1. Test Case ID: F7IxDy

- Server error

[500] Internal Server Error:

    `{"error":"Unicode error","success":false}`

Reproduce with:

    curl -X POST -H 'Content-Type: application/json' -d '{"text": "\u0080"}' 
    http://127.0.0.1:5123/internal-server-errors/improper-unicode-encoding
```

## Customizing Test Execution

Customize the Schemathesis test run using action parameters:

```yaml
- name: Set access token
  run: echo "TOKEN=super-secret" >> $GITHUB_ENV

- uses: schemathesis/action@v1
  with:
    schema: 'http://127.0.0.1:5123/openapi.json'
    args: '-H "Authorization: Bearer ${{ env.TOKEN }}" --max-response-time=200'
```

This example adds an authorization header and sets a 200ms maximum response time threshold.

For all available options, see the [GitHub Action documentation](https://github.com/schemathesis/action).
