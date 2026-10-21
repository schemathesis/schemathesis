#!/bin/sh
# Checks a built Schemathesis image the way users run it.
# Usage: scripts/docker-smoke.sh <image>
set -eu

IMAGE="$1"
VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml)

docker run --rm --entrypoint sh -e EXPECTED_VERSION="$VERSION" "$IMAGE" -c '
set -eu
fail() { echo "FAIL: $*"; exit 1; }

# The working directory is where users mount their files; project files there leak into their tools.
contents=$(ls -A /app)
[ "$contents" = "hooks.py" ] || fail "unexpected files in /app: $(echo $contents)"

cd /app
location=$(python -c "import schemathesis; print(schemathesis.__file__)")
case "$location" in /opt/venv/*) ;; *) fail "schemathesis imported from $location" ;; esac

schemathesis --version | grep -qx "schemathesis, version $EXPECTED_VERSION" || fail "version is not $EXPECTED_VERSION"

mkdir -p /tmp/api && cd /tmp/api
printf "ok" > health
printf "%s" "{\"openapi\": \"3.0.2\", \"info\": {\"title\": \"Smoke\", \"version\": \"1\"}, \"paths\": {\"/health\": {\"get\": {\"responses\": {\"200\": {\"description\": \"OK\"}}}}}}" > openapi.json
python -m http.server 8081 --bind 127.0.0.1 > /dev/null 2>&1 &
sleep 1
schemathesis run http://127.0.0.1:8081/openapi.json --phases fuzzing --checks not_a_server_error --max-examples 5 > /tmp/run.log 2>&1 || { cat /tmp/run.log; fail "schemathesis run"; }
grep -q "Schema Coverage" /tmp/run.log || { cat /tmp/run.log; fail "default hooks did not load"; }

mkdir -p /app/tests && printf "def test_ok():\n    pass\n" > /app/tests/test_ok.py
python -m pytest -q -p no:cacheprovider /app/tests > /tmp/pytest.log 2>&1 || { cat /tmp/pytest.log; fail "pytest under /app"; }

echo "OK"
'
