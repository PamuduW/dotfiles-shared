#!/usr/bin/env bash
# Every declaration of the CONTRACT revision, compared.
#
# agentbot/tests/check_shared_contract.sh compares Agentbot's two resolvers to
# each other. Nothing compared Dotfiles' declaration to either, or to the
# integer in this repository, so three of the four could agree while the fourth
# drifted -- and the first sign would be an operator's run stopping.
#
# Usage: check_contract_agreement.sh <shared> [consumer ...]
#
# A consumer that is not checked out is skipped, not failed: this runs in CI
# where all three are present, and by hand where they may not be.
set -uo pipefail

shared_root="${1:?usage: check_contract_agreement.sh <shared> [consumer ...]}"
shift

fail=0
declare -A seen=()

record() {
	local label="$1" value="$2"
	printf '  %-46s %s\n' "$label" "${value:-<unreadable>}"
	[[ -n "$value" ]] || {
		fail=1
		return
	}
	seen["$value"]=1
}

read -r contract <"$shared_root/CONTRACT" || contract=''
record "dotfiles-shared/CONTRACT" "${contract//[[:space:]]/}"

for consumer in "$@"; do
	[[ -d "$consumer" ]] || {
		printf '  %-46s (not checked out)\n' "$(basename "$consumer")"
		continue
	}
	name="$(basename "$consumer")"

	resolver="$consumer/scripts/lib/shared_resolve.sh"
	if [[ -f "$resolver" ]]; then
		value="$(sed -n 's/^DOTFILES_SHARED_CONTRACT_REQUIRED=\([0-9]*\).*/\1/p' "$resolver" | head -1)"
		record "$name shared_resolve.sh" "$value"
	fi

	paths="$consumer/src/shared_paths.py"
	if [[ -f "$paths" ]]; then
		value="$(sed -n 's/^CONTRACT_REQUIRED = \([0-9]*\).*/\1/p' "$paths" | head -1)"
		record "$name shared_paths.py" "$value"
	fi
done

if ((${#seen[@]} > 1)); then
	printf '\n  Error: the declarations disagree: %s\n' "${!seen[*]}" >&2
	printf '  Raise CONTRACT here and in every consumer in the same batch.\n' >&2
	exit 1
fi
((fail == 0)) || {
	printf '\n  Error: a declaration could not be read.\n' >&2
	exit 1
}
printf '\n  All declarations agree on CONTRACT %s\n' "${!seen[*]}"
