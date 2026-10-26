# shellcheck shell=bash
# Gate the repositories a command depends on, in one pass.
#
# Both products need the same thing before they do any work: check every
# repository they load code from, shared library first, pull what is behind,
# and stop if anything moved. Only the runner differs -- each product has its
# own binding over the repository state machine, with its own result
# vocabulary -- so the runner is injected and the ordering, aggregation and
# exit contract live here.
#
# It was written twice first, once per product, which is how this file exists.
#
# Contract, matching the runners':
#
#   0  every repository is current, continue
#   1  a repository stopped or its pull was declined
#   2  a repository moved -- the caller must restart
#
# Two is not advisory. Pulling replaces files a running shell has already
# sourced, and the CONTRACT compatibility the run checked at startup was
# checked against the checkout that pull just replaced.

# Run one gate pass. `runner` is a function taking one target name and
# returning the contract above; `targets` are the product's own names for the
# repositories, in the order they must be checked.
repo_gate_run() {
	local runner="$1"
	shift
	local target changed=0 rc

	for target in "$@"; do
		rc=0
		"$runner" "$target" || rc=$?
		case "$rc" in
		0) ;;
		2) changed=1 ;;
		# A stop is reported by the runner, which owns the result. Returning
		# immediately is what keeps a declined shared pull from going on to
		# ask about the product's own repository.
		*) return "$rc" ;;
		esac
	done

	((changed)) && return 2
	return 0
}

# Copy one result array over another, by name. Bash has no assignment for
# associative arrays, so the keys are walked.
#
# Whichever repository stops becomes the reported result, so a caller's decline
# and stop handling works without having to know which one it was.
repo_gate_adopt() {
	local -n _adopt_dst="$1"
	local -n _adopt_src="$2"
	local key
	_adopt_dst=()
	for key in "${!_adopt_src[@]}"; do
		_adopt_dst["$key"]="${_adopt_src[$key]}"
	done
}

# Explicit qualification checks frozen sources instead of moving repository refs.
# shellcheck disable=SC2034  # Populates the caller's result through a nameref.
repo_gate_frozen() {
	local repo_dir="$1" role="$2" result_name="$3" label="$4" shared_root selected_root
	local -n frozen_result_ref="$result_name"
	_repo_update_result_init "$result_name" "$repo_dir" "$label"
	if [[ -z "${_SOFTWARE_CATALOG_QUALIFICATION_CONTEXT:-}" ]] || ! declare -F software_catalog_begin >/dev/null; then
		return 1
	fi
	shared_root="$(software_catalog_qualification_source dotfiles-shared)" || return 1
	if ! software_catalog_begin "$shared_root"; then
		frozen_result_ref["reason"]=qualification-invalid
		return 1
	fi
	selected_root="$(software_catalog_qualification_source "$role")" || return 1
	[[ "$(realpath -e -- "$repo_dir")" == "$selected_root" ]] || return 1
	frozen_result_ref["state"]=current
	frozen_result_ref["reason"]=qualification-frozen
	frozen_result_ref["safe"]=1
	frozen_result_ref["outcome"]=current
}
