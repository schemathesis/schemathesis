#!/bin/bash
# This is a helper for shrinkray to minify API schemas where Schemathesis does not behave correctly

if [ $# -lt 2 ]; then
  echo "Usage: $0 <command> <needle>"
  exit 1
fi

COMMAND="$1"
NEEDLE="$2"

# Run the command and capture stdout + stderr
output=$($COMMAND 2>&1)

# Check if needle is present in output
if echo "$output" | grep -q "$NEEDLE"; then
  exit 0
else
  exit 1
fi
