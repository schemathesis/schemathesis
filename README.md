# schemathesis

## Usage

*TODO!*

## Code formatting

In order to maintain code formatting consistency we use [black](https://github.com/ambv/black/)
to format the python files. A pre-commit hook that formats the code is provided but it needs to be
installed on your local git repo, so...

In order to install the pre-commit framework run `pip install pre-commit`
or if you prefer homebrew `brew install pre-commit`

Once you have installed pre-commit just run `pre-commit install` on your repo folder

## Testing

To run all tests:

```
tox
```

Note that tox doesn't know when you change the `requirements.txt`
and won't automatically install new dependencies for test runs.
Run `pip install tox-battery` to install a plugin which fixes this silliness.
