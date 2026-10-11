#!/usr/bin/env bash
# capture.sh <commit> <python> <outdir> [run-id...]
# Exports <commit> into a fresh directory and runs the eval runner's commands against fake CLIs.
# For every command it records stdout, stderr, the exit code, the files written (snap.py) and the
# CLI calls made (fake.py), then normalizes paths, timestamps and durations. Each run-id names a
# run in evals/runs/ to copy in and exercise with report, calibrate and check; the original is
# never written. Compare two captures of the same python with compare.sh.
set -uo pipefail

if (( $# < 3 )); then
  echo "usage: $0 <commit> <python> <outdir> [run-id...]" >&2
  exit 2
fi
commit=$1 py=$2 out=$3
shift 3
runs=("$@")

H=$(cd "$(dirname "$0")" && pwd)
REPO=$(git -C "$H" rev-parse --show-toplevel)
runs_clean() { env -i HOME="$HOME" PATH=/usr/bin:/bin "$@" >/dev/null 2>&1; }
# Commands run with a clean environment, so skip version-manager shims that need the caller's.
UV=$(for c in ${UV:-} $(type -ap uv); do runs_clean "$c" --version && { echo "$c"; break; }; done)
[[ -n $UV ]] || { echo "no uv runs in a clean environment; set UV=/path/to/uv" >&2; exit 1; }
PY=$(python3 -c 'import sys; print(sys.executable)') && runs_clean "$PY" -c '' \
  || { echo "python3 does not run in a clean environment" >&2; exit 1; }
GIT=$(command -v git)
# Fixed per python, so uv reuses one script environment; one capture per python at a time.
work=${TMPDIR:-/tmp}/tw-capture/py$py

rm -rf "$work" "$out"
mkdir -p "$work/repo" "$work/tmp" "$work/state" "$work/bin" "$out"
out=$(cd "$out" && pwd)  # run() writes from inside the copy
git -C "$REPO" archive "$commit" | tar -x -C "$work/repo"
mkdir -p "$work/repo/evals/runs"
for r in ${runs[@]+"${runs[@]}"}; do
  cp -Rp "$REPO/evals/runs/$r" "$work/repo/evals/runs/" || exit 1
done
git -C "$REPO" rev-parse "$commit" >"$out/commit"

# The fakes, and git for the trigger runs. The real CLIs are not on this PATH.
for cli in claude codex; do
  printf '#!/bin/sh\nexec "%s" -I "%s" %s "$@"\n' "$PY" "$H/fake.py" "$cli" >"$work/bin/$cli"
  chmod +x "$work/bin/$cli"
done
ln -s "$GIT" "$work/bin/git"
path="$work/bin:/usr/bin:/bin"
for cli in claude codex; do
  [[ $(PATH=$path command -v $cli) == "$work/bin/$cli" ]] || { echo "PATH reaches a real $cli" >&2; exit 1; }
done

"$PY" -I "$H/snap.py" "$work/repo" "$work/snap.json"
step=0
run() {  # run <label> <runner args...>
  step=$((step + 1))
  local id
  id=$(printf '%02d-%s' "$step" "$1")
  shift
  echo "$*" >"$out/$id.cmd"
  (cd "$work/repo" && env -i HOME="$HOME" PATH="$path" TMPDIR="$work/tmp/" FAKE_STATE="$work/state" \
    COLUMNS=80 LANG=en_US.UTF-8 ${UV_CACHE_DIR:+UV_CACHE_DIR="$UV_CACHE_DIR"} \
    "$UV" run --quiet --locked --python "$py" evals/run.py "$@" >"$out/$id.out" 2>"$out/$id.err")
  echo $? >"$out/$id.exit"
  [[ -f $out/$id.out ]] || { echo "step $id left no output" >&2; exit 1; }
  "$PY" -I "$H/snap.py" "$work/repo" "$work/snap.json" "$out/$id.files"
  if compgen -G "$work/state/log/*" >/dev/null; then
    mkdir -p "$out/$id.calls" && mv "$work/state/log/"* "$out/$id.calls/"
  fi
}

run lint lint
run selftest selftest
run help --help
run report-help report --help
run calibrate-help calibrate --help
run report-nope report --run-id nope
run report-cases-zzz report --cases zzz
run report-harness-gpt report --harness gpt
run calibrate-nope calibrate --run-id nope

for r in ${runs[@]+"${runs[@]}"}; do
  run "$r-report-cases-zzz" report --run-id "$r" --cases zzz
  run "$r-report" report --run-id "$r"
  run "$r-report-claude" report --run-id "$r" --harness claude
  run "$r-report-subset" report --run-id "$r" --harness codex --cases 1,rfc-constraints --conditions A,C --trials 2
  run "$r-calibrate" calibrate --run-id "$r"
  run "$r-calibrate-codex" calibrate --run-id "$r" --harness codex --groups 7
  run "$r-check" check --run-id "$r"
  run "$r-report-again" report --run-id "$r"
done

F=(--run-id fake --jobs 1 --trials 2 --trigger-runs 2 --trigger-timeout 3
   --claude-model fake-sonnet --codex-effort low --judge-effort high)
run fake-generate generate "${F[@]}"
run fake-check check "${F[@]}"
run fake-judge judge "${F[@]}"
run fake-report report "${F[@]}"
run fake-generate-again generate "${F[@]}"
run fake-judge-again judge "${F[@]}"
run fake-triggers triggers "${F[@]}"
run fake-report-again report "${F[@]}"
run fake-calibrate calibrate --run-id fake --trials 2

G=(--run-id fake2 --jobs 1 --trials 1 --trigger-runs 1 --trigger-timeout 3 --cases 2,vague-pr,sensitive-runbook
   --codex-model fake-codex --claude-effort max --judge-harness cross)
run fake2-all all "${G[@]}"
run fake2-all-again all "${G[@]}"

ls -A "$work/tmp" >"$out/zz-tmp-leftovers.txt"
"$PY" -I "$H/normalize.py" "$out" "$work"
echo "$step steps" >"$out/complete"
echo "captured $(cut -c1-7 "$out/commit") on python $py in $out ($step steps)"
