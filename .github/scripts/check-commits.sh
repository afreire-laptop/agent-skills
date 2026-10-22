#!/usr/bin/env bash
# Checks commit headers against the repository's Conventional Commits rules, and that each
# commit's type matches the release policy in README.md ("Versioning"):
#   - a commit that changes a skill, skills/<name>/ apart from its CHANGELOG.md, is feat, fix,
#     or revert, or marks a breaking change with !; it changes that one skill, and its scope is
#     the skill's name;
#   - any other commit is none of those, so it makes no release.
# release-please assigns a commit to every skill whose files it changes and ignores the scope,
# so a releasing commit that changed two skills would release both.
# Usage: check-commits.sh <base> <head> [pull request title]
# Checks the commits in base..head, or only head when base is empty, all zeros, or unknown.
# A squash merge uses the pull request title as the commit header, so it is checked too.
set -euo pipefail

base=${1:-} head=$2 title=${3:-}
header_re='^(feat|fix|docs|test|ci|chore|refactor|build|perf|style|revert)\(([a-z0-9-]+)\)(!?): [a-z0-9].*[^.]$'
failures=0

changed_skills() {  # changed_skills <changed paths, one per line>: the skills they change, space-separated
  sed -n -e '\#^skills/[^/]*/CHANGELOG\.md$#d' -e 's#^skills/\([^/]*\)/.*#\1#p' <<<"$1" | sort -u | paste -sd ' ' -
}

check() {  # check <header> <changed paths> <where>
  local header=$1 where=$3 releases=no skills
  skills=$(changed_skills "$2")
  if (( ${#header} > 72 )) || ! [[ $header =~ $header_re ]]; then
    echo "::error::$where: header must be 'type(scope): summary', lowercase, without a final period, at most 72 characters: $header"
    failures=$((failures + 1))
    return
  fi
  local type=${BASH_REMATCH[1]} scope=${BASH_REMATCH[2]} breaking=${BASH_REMATCH[3]}
  if [[ $type =~ ^(feat|fix|revert)$ || -n $breaking ]]; then
    releases=yes
  fi
  if [[ -n $skills && $releases == no ]]; then
    echo "::error::$where: changes skill(s) $skills, but '$type' makes no release; use feat, fix, or '!': $header"
    failures=$((failures + 1))
  elif [[ -z $skills && $releases == yes ]]; then
    echo "::error::$where: changes no skill, but '$type$breaking' would make a release: $header"
    failures=$((failures + 1))
  elif [[ $skills == *' '* ]]; then
    echo "::error::$where: changes skills $skills; a releasing commit changes one skill: $header"
    failures=$((failures + 1))
  elif [[ -n $skills && $scope != "$skills" ]]; then
    echo "::error::$where: changes skills/$skills/, so its scope must be '$skills', not '$scope': $header"
    failures=$((failures + 1))
  fi
}

if [[ -z $base || $base =~ ^0+$ ]] || ! git cat-file -e "$base^{commit}" 2>/dev/null; then
  commits=$head
else
  commits=$(git rev-list --no-merges "$base..$head")
fi
for sha in $commits; do
  check "$(git log -1 --format=%s "$sha")" "$(git diff-tree --root --no-commit-id --name-only -r "$sha")" "commit ${sha:0:7}"
done
if [[ -n $title ]]; then
  check "$title" "$(git diff --name-only "$base...$head")" "pull request title"
fi

if (( failures )); then
  echo "$failures commit message problem(s); see README.md, \"Versioning\"."
  exit 1
fi
echo "commit messages: ok ($(wc -w <<<"$commits" | tr -d ' ') commit(s)${title:+ and the pull request title})"
