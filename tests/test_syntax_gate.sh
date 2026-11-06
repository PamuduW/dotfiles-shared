#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
gate="$(awk '/name: Bash syntax$/ {getline; sub(/^ *run: /, ""); print; exit}' "$ROOT/.github/workflows/validate.yml")"
[[ -n "$gate" ]]
fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT
mkdir "$fixture/scripts" "$fixture/tests"
printf 'echo valid\n' >"$fixture/scripts/first.sh"
printf 'if then\n' >"$fixture/tests/broken second.sh"
if (cd "$fixture" && bash -e -o pipefail -c "$gate") >"$fixture/output" 2>&1; then
	printf 'not ok - workflow accepted an invalid later script\n' >&2
	exit 1
fi
grep -Fq 'broken second.sh' "$fixture/output"
printf 'echo valid\n' >"$fixture/tests/broken second.sh"
(cd "$fixture" && bash -e -o pipefail -c "$gate")
printf 'ok - workflow checks every script and handles spaces\n'
