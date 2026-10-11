#!/usr/bin/env bash
# Refuses to let release-please tag a minor or major release whose evidence is not in the
# release commit: evals/results/vX.Y.Z.md with the full eval report and a
# "## Judge spot-check" section holding the human calibration result (README.md, "Versioning").
# Patch releases and pushes with no release pending pass.
set -euo pipefail

manifest=.release-please-manifest.json
version=$(jq -r '."."' "$manifest")
latest=$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || echo v0.0.0)
latest=${latest#v}

if [[ $version == "$latest" ]]; then
  echo "No release pending: the manifest and the latest tag are both $version."
  exit 0
fi
IFS=. read -r new_major new_minor _ <<<"$version"
IFS=. read -r old_major old_minor _ <<<"$latest"
if [[ $new_major == "$old_major" && $new_minor == "$old_minor" ]]; then
  echo "Patch release $version: the full eval and the calibration check are not required."
  exit 0
fi

# The commit release-please tags: the one that moved the manifest to this version on main.
release_commit=$(git log -1 --first-parent --format=%H -- "$manifest")
evidence=evals/results/v$version.md
if ! git cat-file -e "$release_commit:$evidence" 2>/dev/null; then
  echo "::error::Release $version needs $evidence in its release commit ${release_commit:0:7}, so nothing was tagged. README.md, \"Versioning\", says how to recover."
  exit 1
fi
if ! git show "$release_commit:$evidence" | grep -q '^## Judge spot-check'; then
  echo "::error::$evidence has no \"## Judge spot-check\" section with the human calibration result."
  exit 1
fi
echo "Evidence for $version: $evidence in ${release_commit:0:7}."
