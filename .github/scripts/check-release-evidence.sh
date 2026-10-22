#!/usr/bin/env bash
# Refuses a skill's minor or major release whose evidence is not in its release commit
# (README.md, "Versioning"). The commit must hold the skill's trigger suite,
# evals/suites/<skill>/triggers.json, and the release report, evals/suites/<skill>/results/vX.Y.Z.md.
# When the suite also has quality cases (cases.json), the report needs a "## Judge spot-check"
# section with the human calibration result.
# The release workflow runs it before release-please tags; CI runs it on each pull request, where
# a release pull request's head is that skill's release commit. Patch releases, and skills with no
# release pending, pass.
set -euo pipefail

manifest=.release-please-manifest.json
config=release-please-config.json
failures=0
if [[ ${GITHUB_EVENT_NAME:-} == pull_request ]]; then
  next="Don't merge yet: merge the results into main first, as README.md, \"Versioning\", step 2 says."
else
  next="Nothing was tagged. README.md, \"Versioning\", says how to recover."
fi

fail() {  # fail <message>
  echo "::error::$1 $next"
  failures=$((failures + 1))
}

release_commit() {  # release_commit <path> <version>: the commit that set the path's manifest version
  local sha
  for sha in $(git log --first-parent --format=%H -- "$manifest"); do
    if [[ $(git show "$sha^:$manifest" 2>/dev/null | jq -r --arg p "$1" '.[$p] // empty') != "$2" ]]; then
      echo "$sha"
      return
    fi
  done
}

while read -r path version; do
  skill=$(jq -r --arg p "$path" '.packages[$p].component // empty' "$config")
  if [[ -z $skill ]]; then
    fail "$path has no component in $config, so its release tags would not name the skill."
    continue
  fi
  latest=$(git describe --tags --abbrev=0 --match "$skill-v[0-9]*" 2>/dev/null || echo "$skill-v0.0.0")
  latest=${latest#"$skill-v"}
  if [[ $version == "$latest" ]]; then
    echo "$skill: no release pending; the manifest and the latest tag are both $version."
    continue
  fi
  IFS=. read -r new_major new_minor _ <<<"$version"
  IFS=. read -r old_major old_minor _ <<<"$latest"
  if [[ $new_major == "$old_major" && $new_minor == "$old_minor" ]]; then
    echo "$skill: patch release $version; the full eval and the calibration check are not required."
    continue
  fi

  commit=$(release_commit "$path" "$version")
  suite=evals/suites/$skill
  evidence=$suite/results/v$version.md
  if [[ -z $commit ]]; then
    fail "Release $skill $version: no commit on this branch sets $path to $version in $manifest."
  elif ! git cat-file -e "$commit:$suite/triggers.json" 2>/dev/null; then
    fail "Release $skill $version needs its trigger suite, $suite/triggers.json, in its release commit ${commit:0:7}."
  elif ! git cat-file -e "$commit:$evidence" 2>/dev/null; then
    fail "Release $skill $version needs $evidence in its release commit ${commit:0:7}."
  elif git cat-file -e "$commit:$suite/cases.json" 2>/dev/null &&
       ! git show "$commit:$evidence" | grep -q '^## Judge spot-check'; then
    fail "$evidence has no \"## Judge spot-check\" section with the human calibration result."
  else
    echo "$skill: evidence for $version is $evidence in ${commit:0:7}."
  fi
done < <(jq -r 'to_entries[] | "\(.key) \(.value)"' "$manifest")

if (( failures )); then
  exit 1
fi
