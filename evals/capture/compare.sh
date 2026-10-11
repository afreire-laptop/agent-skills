#!/usr/bin/env bash
# compare.sh <capture> <capture>: diff two captures from capture.sh. Exits 0 when they are
# identical, 1 when they differ, 2 when either capture is incomplete.
set -uo pipefail
for c in "$1" "$2"; do
  if [[ ! -f $c/complete ]]; then
    echo "incomplete capture: $c"
    exit 2
  fi
  calls=$(find "$c" -path '*.calls/*' -type f | wc -l | tr -d ' ')
  echo "$c: commit $(cut -c1-7 "$c/commit"), $(cat "$c/complete"), $calls CLI calls"
done
if diff -r -x commit "$1" "$2"; then echo "IDENTICAL"; else echo "DIFFERENT"; exit 1; fi
