#!/usr/bin/env bash
# Regenerate img/demo.gif. Requires asciinema 3 and agg >= 1.6.
set -euo pipefail

SELF="$(realpath "$0")"
cd "$(dirname "$SELF")"

# The demo API's load balancer answers TRACE with 405 and no `Allow` header; out of the app's control.
CMD='uvx schemathesis run https://example.schemathesis.io/openapi.json --exclude-checks unsupported_method'

if [[ "${1:-}" == "--play" ]]; then
    printf '\033[1;36m$\033[0m '
    for ((i = 0; i < ${#CMD}; i++)); do
        printf '%s' "${CMD:i:1}"
        sleep 0.06
    done
    sleep 0.5
    echo
    eval "$CMD" || true
    sleep 2
    exit 0
fi

# Warm the uvx cache so the recording has no download output.
uvx schemathesis --version >/dev/null

CAST="$(mktemp --suffix .cast)"
trap 'rm -f "$CAST"' EXIT
asciinema rec --overwrite --quiet --window-size 110x40 -c "$SELF --play" "$CAST"

# Cap idle gaps at 2s, then hold for 8s (4s at 2x) once the first server error is on screen.
python3 - "$CAST" <<'EOF'
import json, sys

path = sys.argv[1]
header, *lines = open(path).read().splitlines()
events = [json.loads(line) for line in lines]
for event in events:
    event[0] = min(event[0], 2)
first = next(i for i, event in enumerate(events) if "- Server error" in event[2])
events[first + 1][0] = 8
with open(path, "w") as fd:
    fd.write("\n".join([header, *map(json.dumps, events)]) + "\n")
EOF

agg --quiet --speed 2 --idle-time-limit 8 --last-frame-duration 8 --font-size 14 "$CAST" demo.gif
