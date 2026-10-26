# shellcheck shell=bash

_SOFTWARE_CATALOG_QUALIFICATION_CONTEXT=''
_SOFTWARE_CATALOG_QUALIFICATION_DIGEST=''
export -n _SOFTWARE_CATALOG_QUALIFICATION_CONTEXT _SOFTWARE_CATALOG_QUALIFICATION_DIGEST
declare -gA _SOFTWARE_CATALOG_QUALIFICATION_SOURCES=()

_software_catalog_qualification_load() {
	local expected_root="${1:-}" descriptor context_program expected_context="$_SOFTWARE_CATALOG_QUALIFICATION_DIGEST"
	local -a fields=()
	SOFTWARE_CATALOG_SNAPSHOT=''
	SOFTWARE_CATALOG_DIGEST=''
	SOFTWARE_CATALOG_TABLE_DIGEST=''
	_SOFTWARE_CATALOG_QUALIFICATION_SOURCES=()
	context_program="${BASH_SOURCE[0]%/*}/python/catalog_context.py"
	descriptor="$(python3 "$context_program" "$_SOFTWARE_CATALOG_QUALIFICATION_CONTEXT")" || return 2
	mapfile -t fields <<<"$descriptor"
	[[ "${#fields[@]}" == 8 && "${fields[1]}" =~ ^[0-9a-f]{64}$ && "${fields[2]}" =~ ^[0-9a-f]{64}$ &&
		"${fields[3]}" =~ ^[0-9a-f]{40}$ && "${fields[4]}" =~ ^[0-9a-f]{64}$ ]] || return 2
	[[ -z "$expected_context" || "$expected_context" == "${fields[4]}" ]] || return 2
	if [[ -n "$expected_root" ]]; then
		[[ "$(cd -- "$expected_root" && pwd -P)" == "${fields[5]}" ]] || return 2
	fi
	SOFTWARE_CATALOG_SNAPSHOT="${fields[0]}"
	SOFTWARE_CATALOG_DIGEST="${fields[1]}"
	SOFTWARE_CATALOG_TABLE_DIGEST="${fields[2]}"
	SOFTWARE_CATALOG_REVISION="${fields[3]}"
	_SOFTWARE_CATALOG_QUALIFICATION_DIGEST="${fields[4]}"
	_SOFTWARE_CATALOG_QUALIFICATION_SOURCES=(["dotfiles-shared"]="${fields[5]}" [dotfiles]="${fields[6]}" [agentbot]="${fields[7]}")
	export SOFTWARE_CATALOG_SNAPSHOT SOFTWARE_CATALOG_DIGEST SOFTWARE_CATALOG_TABLE_DIGEST SOFTWARE_CATALOG_REVISION
}

software_catalog_qualification_source() {
	local name="$1"
	case "$name" in dotfiles | dotfiles-shared | agentbot) ;; *) return 2 ;; esac
	[[ -n "${_SOFTWARE_CATALOG_QUALIFICATION_SOURCES[$name]:-}" ]] || return 2
	printf '%s\n' "${_SOFTWARE_CATALOG_QUALIFICATION_SOURCES[$name]}"
}

software_catalog_qualification_begin() {
	_SOFTWARE_CATALOG_QUALIFICATION_CONTEXT="$1"
	_SOFTWARE_CATALOG_QUALIFICATION_DIGEST=''
	if ! _software_catalog_qualification_load; then
		_SOFTWARE_CATALOG_QUALIFICATION_CONTEXT=''
		_SOFTWARE_CATALOG_QUALIFICATION_DIGEST=''
		SOFTWARE_CATALOG_SNAPSHOT=''
		return 2
	fi
}

_software_catalog_locks() {
	awk -F '\t' '
        $2 == "digest" { digests[$1 SUBSEP $3] = $4 }
        $2 == "lock" { paths[$1 SUBSEP $3] = $4 }
        END { for (key in paths) print paths[key] "\t" digests[key] }
    ' "$1/software.tsv"
}

_software_catalog_verify_locks() {
	local directory="$1" name digest actual parent
	while IFS=$'\t' read -r name digest; do
		[[ -n "$name" ]] || continue
		[[ "$name" =~ ^locks/[A-Za-z0-9_./-]+$ && "$name" != *../* && "$name" != */.. && "$name" != *//* && "$name" != */./* && "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || return 2
		parent="$directory/$name"
		while [[ "$parent" != "$directory" ]]; do
			[[ ! -L "$parent" ]] || return 2
			parent="$(dirname -- "$parent")"
		done
		[[ -f "$directory/$name" ]] || return 2
		actual="$(sha256sum -- "$directory/$name")" || return 2
		[[ "sha256:${actual%% *}" == "$digest" ]] || return 2
	done < <(_software_catalog_locks "$directory")
}

software_catalog_load() {
	local directory="$1" file actual
	SOFTWARE_CATALOG_SNAPSHOT=''
	SOFTWARE_CATALOG_DIGEST=''
	SOFTWARE_CATALOG_TABLE_DIGEST=''
	local -a proof=()
	for file in software.json software.tsv approval approval.json; do
		if [[ ! -f "$directory/$file" || -L "$directory/$file" ]]; then
			printf 'Software catalog is missing a regular %s file; no approved snapshot.\n' "$file" >&2
			return 2
		fi
	done
	mapfile -t proof <"$directory/approval"
	[[ "${#proof[@]}" -eq 4 && "${proof[0]}" == software-approved-v1 ]] || return 2
	for file in 1 2 3; do [[ "${proof[$file]}" =~ ^[0-9a-f]{64}$ ]] || return 2; done
	local -a names=(software.json software.tsv approval.json)
	for file in 0 1 2; do
		actual="$(sha256sum -- "$directory/${names[$file]}")" || return 2
		[[ "${actual%% *}" == "${proof[$((file + 1))]}" ]] || return 2
	done
	IFS= read -r actual <"$directory/software.tsv" || return 2
	[[ "$actual" == $'#software-catalog-v1\t'"${proof[1]}" ]] || return 2
	_software_catalog_verify_locks "$directory" || return 2
	SOFTWARE_CATALOG_SNAPSHOT="$directory"
	SOFTWARE_CATALOG_DIGEST="${proof[1]}"
	SOFTWARE_CATALOG_TABLE_DIGEST="${proof[2]}"
	export SOFTWARE_CATALOG_SNAPSHOT SOFTWARE_CATALOG_DIGEST SOFTWARE_CATALOG_TABLE_DIGEST
}

software_catalog_get() {
	local key="$1" field="$2" target="${3:-any}" actual abi=""
	[[ -n "${SOFTWARE_CATALOG_SNAPSHOT:-}" ]] || return 2
	[[ "$key" =~ ^[a-z][a-z0-9_.-]*$ && "$field" =~ ^[a-z][a-z0-9_.-]*$ && "$target" =~ ^[a-z0-9-]+$ ]] || return 2
	actual="$(sha256sum -- "$SOFTWARE_CATALOG_SNAPSHOT/software.tsv")" || return 2
	[[ "${actual%% *}" == "$SOFTWARE_CATALOG_TABLE_DIGEST" ]] || return 2
	case "$target" in wsl2-amd64 | wsl2-arm64) abi="linux-${target#wsl2-}" ;; esac
	awk -F '\t' -v key="$key" -v field="$field" -v target="$target" -v abi="$abi" '
        $1 == key && $2 == "blockers" { blocked = 1 }
        $1 == key && ($2 == "version" || $2 == "revision") && $3 == target { specific = 1 }
        $1 == key && ($2 == "version" || $2 == "revision") && $3 == abi { abi_specific = 1 }
        $1 == key && $2 == field && $3 == target { value = $4; count++ }
        $1 == key && $2 == field && $3 == abi { abi_value = $4; abi_count++ }
        $1 == key && $2 == field && $3 == "any" { fallback = $4; fallback_count++ }
        END {
            metadata = field == "owner" || field == "kind" || field == "source" || field ~ /^discovery\./
            if (blocked || count > 1 || (!specific && abi_count > 1)) exit 2
            if (count == 1) print value
            else if (!specific && abi_count == 1) print abi_value
            else if ((metadata || (!specific && !abi_specific)) && fallback_count == 1) print fallback
            else exit 2
        }' "$SOFTWARE_CATALOG_SNAPSHOT/software.tsv"
}

_software_catalog_cache_matches() {
	local staging="$1" destination="$2" name
	[[ -d "$destination" && ! -L "$destination" ]] || return 2
	software_catalog_load "$destination" || return 2
	for name in software.json software.tsv approval approval.json; do
		[[ ! -L "$destination/$name" ]] && cmp -s -- "$staging/$name" "$destination/$name" || return 2
	done
}

_software_catalog_freeze() (
	local root="$1" cache="$2" revision="$3" origin staging name destination digest parent move_error
	origin="$(git -C "$root" remote get-url origin)" || return 2
	case "${origin%.git}" in
	git@github.com:PamuduW/dotfiles-shared | https://github.com/PamuduW/dotfiles-shared | ssh://git@github.com/PamuduW/dotfiles-shared) ;;
	*)
		printf 'Untrusted software catalog origin.\n' >&2
		return 2
		;;
	esac
	[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || return 2
	git -C "$root" merge-base --is-ancestor "$revision" refs/remotes/origin/main || return 2
	parent="$cache"
	while [[ "$parent" != / && "$parent" != . ]]; do
		[[ ! -L "$parent" ]] || return 2
		parent="$(dirname -- "$parent")"
	done
	umask 077
	mkdir -p -- "$cache" || return 2
	[[ -O "$cache" && "$(stat -c %a -- "$cache")" == 700 ]] || return 2
	staging="$(mktemp -d "$cache/.catalog-XXXXXX")" || return 2
	trap 'rm -rf -- "$staging"' EXIT
	for name in software.json software.tsv approval approval.json; do
		git -C "$root" show "$revision:catalog/$name" >"$staging/$name" || return 2
	done
	while IFS=$'\t' read -r name digest; do
		[[ -n "$name" ]] || continue
		[[ "$name" =~ ^locks/[A-Za-z0-9_./-]+$ && "$name" != *../* && "$name" != */.. && "$name" != *//* && "$name" != */./* && "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || return 2
		mkdir -p -- "$(dirname -- "$staging/$name")" || return 2
		git -C "$root" show "$revision:catalog/$name" >"$staging/$name" || return 2
	done < <(_software_catalog_locks "$staging")
	software_catalog_load "$staging" || return 2
	destination="$cache/$revision"
	if [[ -e "$destination" || -L "$destination" ]]; then
		_software_catalog_cache_matches "$staging" "$destination" || return 2
	else
		if ! move_error="$(mv -T -- "$staging" "$destination" 2>&1)"; then
			_software_catalog_cache_matches "$staging" "$destination" || {
				printf '%s\n' "$move_error" >&2
				return 2
			}
		fi
	fi
	printf '%s\n' "$destination"
)

software_catalog_begin() {
	local root="$1" cache="${2:-${XDG_CACHE_HOME:-$HOME/.cache}/software-catalog}" revision snapshot
	if [[ -n "$_SOFTWARE_CATALOG_QUALIFICATION_CONTEXT" ]]; then
		_software_catalog_qualification_load "$root"
		return $?
	fi
	revision="${SOFTWARE_CATALOG_REVISION:-}"
	if [[ -z "$revision" ]]; then
		revision="$(git -C "$root" rev-parse refs/remotes/origin/main)" || return 2
	fi
	snapshot="$(_software_catalog_freeze "$root" "$cache" "$revision")" || return 2
	software_catalog_load "$snapshot" || return 2
	SOFTWARE_CATALOG_REVISION="$revision"
	export SOFTWARE_CATALOG_REVISION
}
