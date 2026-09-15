# shellcheck shell=bash
# The GitHub token screen, shared by both products.
#
# It was written twice -- 201 lines in Agentbot, 181 in Dotfiles, thirteen of
# fourteen functions matching one for one -- and the copies had drifted: only
# Agentbot honoured an inherited terminal descriptor, and only Dotfiles said
# *why* a removal failed. This is the union, so neither product loses the half
# it had.
#
# Binding contract. A consumer sets what differs and calls github_token_menu:
#
#   GITHUB_TOKEN_MENU_ROOT        breadcrumb root, e.g. Agentbot or Dotfiles
#   GITHUB_TOKEN_MENU_COLS_FN     function printing the terminal width
#   GITHUB_TOKEN_MENU_REFRESH_FN  function repointing the DOTFILES_TTY_* seam
#                                 before descriptors are resolved, where the
#                                 product has its own seam to refresh
#   GITHUB_TOKEN_TTY_INPUT        explicit terminal overrides, used by tests
#   GITHUB_TOKEN_TTY_OUTPUT
#   GITHUB_TOKEN_TTY_COLS
#
# Everything the screen does to the token itself -- reading, fingerprinting,
# validating, verifying with GitHub, writing, removing -- belongs to
# github_token.sh beside this file. This is presentation and confirmation only.

: "${GITHUB_TOKEN_MENU_ROOT:=Dotfiles}"
: "${GITHUB_TOKEN_MENU_COLS_FN:=menu_tty_cols}"
: "${GITHUB_TOKEN_MENU_REFRESH_FN:=}"

_github_token_menu_open_fds() {
	local in_path out_path
	# An explicit override wins outright. Otherwise the product is asked to
	# refresh its seam first, because an inherited descriptor is only correct
	# once the seam pointing at it is current. Reopening a path would restart
	# the stream and re-read the choice that got us here, so an inherited
	# descriptor is duplicated rather than reopened.
	if [[ -n "${GITHUB_TOKEN_TTY_INPUT:-}" ]]; then
		in_path="$GITHUB_TOKEN_TTY_INPUT"
		exec {GITHUB_TOKEN_MENU_IN_FD}<"$in_path"
	else
		[[ -n "$GITHUB_TOKEN_MENU_REFRESH_FN" ]] && "$GITHUB_TOKEN_MENU_REFRESH_FN"
		if tty_use_input_fd; then
			exec {GITHUB_TOKEN_MENU_IN_FD}<&"$DOTFILES_TTY_IN_FD"
		else
			in_path="$(tty_input_path)"
			exec {GITHUB_TOKEN_MENU_IN_FD}<"$in_path"
		fi
	fi
	if [[ -n "${GITHUB_TOKEN_TTY_OUTPUT:-}" ]]; then
		out_path="$GITHUB_TOKEN_TTY_OUTPUT"
		exec {GITHUB_TOKEN_MENU_OUT_FD}>"$out_path"
	elif tty_use_output_fd; then
		exec {GITHUB_TOKEN_MENU_OUT_FD}>&"$DOTFILES_TTY_OUT_FD"
	else
		out_path="$(tty_output_path)"
		exec {GITHUB_TOKEN_MENU_OUT_FD}>"$out_path"
	fi
}

_github_token_menu_close_fds() {
	exec {GITHUB_TOKEN_MENU_IN_FD}<&-
	exec {GITHUB_TOKEN_MENU_OUT_FD}>&-
}

_github_token_menu_line() {
	local out_var="$1" prompt="$2" value=''
	printf '%s' "$prompt" >&"$GITHUB_TOKEN_MENU_OUT_FD"
	IFS= read -r value <&"$GITHUB_TOKEN_MENU_IN_FD" || value='q'
	printf -v "$out_var" '%s' "$value"
}

# This screen reads a choice, a secret and a confirmation from one stream, so
# the shared prompts are pointed at its already-open descriptors rather than a
# path. DOTFILES_TTY_*_FD is the seam they read; `local` is dynamic scope in
# Bash, so the callee and everything it calls see these, and the caller's
# values come back untouched.
_github_token_menu_on_seam() {
	# shellcheck disable=SC2034  # Read through dynamic scope by the callee.
	local DOTFILES_TTY_IN_FD="$GITHUB_TOKEN_MENU_IN_FD"
	# shellcheck disable=SC2034  # Read through dynamic scope by the callee.
	local DOTFILES_TTY_OUT_FD="$GITHUB_TOKEN_MENU_OUT_FD"
	"$@"
}

# [y/N], not [y/N/q]. The hint said q for long enough to look deliberate, but
# nothing below reads it: every answer that is not y is no, and all three
# callers -- save, reveal, remove -- treat a no as "go back". A third key that
# does what the second key does is a promise the screen cannot keep.
_github_token_menu_confirm() {
	_github_token_menu_on_seam ui_confirm_yes_no "  ${C_YELLOW:-}$1${C_RESET:-}" true
}

_github_token_menu_pause() {
	_github_token_menu_on_seam ui_pause
}

_github_token_menu_secret() {
	local out_var="$1" prompt="$2" value=''
	# `read -rs` shows nothing at all, so a mistyped token gives no feedback
	# that anything was typed. read_tty_secret masks with * and supports
	# backspace; the SSH passphrase prompt has used it since it was written.
	_github_token_menu_on_seam read_tty_secret value "$prompt" || value='q'
	printf -v "$out_var" '%s' "$value"
}

# What the last action did, shown by the next frame.
#
# Every outcome here used to be printed and then wiped: the loop clears the
# screen and re-renders before the operator can read "GitHub token saved." or
# "Invalid token; nothing was saved." Only the reveal survived, because it
# pauses. Carried into the next frame instead, which is what the checkbox menu
# does with MENU_CB_STATUS_MESSAGE and costs no extra keystroke.
_GITHUB_TOKEN_MENU_STATUS=''

_github_token_menu_say() {
	_GITHUB_TOKEN_MENU_STATUS="$1"
}

_github_token_menu_render() {
	local token='' current='not configured' current_color="${C_DIM:-}"
	local root="$GITHUB_TOKEN_MENU_ROOT"
	local cols="${GITHUB_TOKEN_TTY_COLS:-}"
	[[ -n "$cols" ]] || cols="$("$GITHUB_TOKEN_MENU_COLS_FN")"
	github_token_read token
	if [[ -n "$token" ]]; then
		current="$(github_token_fingerprint "$token")"
		current_color="${C_GREEN:-}"
	elif [[ -e "$(github_token_file)" || -L "$(github_token_file)" ]]; then
		current='saved state is invalid or unsafe'
		current_color="${C_RED:-}"
	fi
	ui_print_header "GitHub token config" "${root} › GitHub token config" "$cols" \
		>&"$GITHUB_TOKEN_MENU_OUT_FD"
	printf '  %sCurrent:%s %s%s%s\n' \
		"${C_BOLD:-}" "${C_RESET:-}" "$current_color" "$current" "${C_RESET:-}" \
		>&"$GITHUB_TOKEN_MENU_OUT_FD"
	printf '  %sSaved outside this repository:%s %s%s%s\n\n' \
		"${C_DIM:-}" "${C_RESET:-}" "${C_CYAN:-}" "$(github_token_file)" "${C_RESET:-}" \
		>&"$GITHUB_TOKEN_MENU_OUT_FD"
	printf '  %sOptional:%s raises public-repository API rate limits.\n' \
		"${C_DIM:-}" "${C_RESET:-}" >&"$GITHUB_TOKEN_MENU_OUT_FD"
	printf '  %sNo repository scopes are needed for this workflow.%s\n\n' \
		"${C_DIM:-}" "${C_RESET:-}" >&"$GITHUB_TOKEN_MENU_OUT_FD"
	printf '  %s\n' \
		"$(ui_format_shortcuts s 'Save or replace' r 'Reveal once' \
			c 'Check with GitHub' d Remove q Back)${C_RESET:-}" \
		>&"$GITHUB_TOKEN_MENU_OUT_FD"
	if [[ -n "$_GITHUB_TOKEN_MENU_STATUS" ]]; then
		printf '\n  %s\n' "$_GITHUB_TOKEN_MENU_STATUS" >&"$GITHUB_TOKEN_MENU_OUT_FD"
	fi
	printf '\n' >&"$GITHUB_TOKEN_MENU_OUT_FD"
}

# Ask GitHub before saving, rather than only checking the shape. A refusal is
# definitive -- the token is wrong, expired or revoked -- so nothing is saved
# and the operator is told which of those it is not. Being unable to ask is not
# a refusal: an operator configuring this offline still gets the normal
# question, with the check named as skipped rather than passed.
_github_token_menu_check() {
	local token="$1" rc=0
	printf '  %sChecking it with GitHub...%s\n' \
		"${C_DIM:-}" "${C_RESET:-}" >&"$GITHUB_TOKEN_MENU_OUT_FD"
	github_token_verify "$token" || rc=$?
	case "$rc" in
	0)
		printf '  %sGitHub accepted it.%s\n\n' \
			"${C_GREEN:-}" "${C_RESET:-}" >&"$GITHUB_TOKEN_MENU_OUT_FD"
		;;
	1)
		_github_token_menu_say "${C_RED:-}GitHub rejected this token; nothing was saved.${C_RESET:-}"
		return 1
		;;
	*)
		printf '  %sCould not reach GitHub to check it; saving without a check.%s\n\n' \
			"${C_YELLOW:-}" "${C_RESET:-}" >&"$GITHUB_TOKEN_MENU_OUT_FD"
		;;
	esac
}

_github_token_menu_save() {
	local token=''
	printf '  %sInput is hidden; only its fingerprint will be shown.%s\n' \
		"${C_DIM:-}" "${C_RESET:-}" >&"$GITHUB_TOKEN_MENU_OUT_FD"
	_github_token_menu_secret token "  ${C_CYAN:-}GitHub token${C_RESET:-} (q cancels): "
	[[ "$token" != q && "$token" != Q && -n "$token" ]] || return 0
	if ! github_token_is_valid "$token"; then
		_github_token_menu_say "${C_RED:-}Invalid token; nothing was saved.${C_RESET:-}"
		return 0
	fi
	printf '\n  %sProposed:%s %s%s%s\n' \
		"${C_DIM:-}" "${C_RESET:-}" "${C_CYAN:-}" \
		"$(github_token_fingerprint "$token")" "${C_RESET:-}" >&"$GITHUB_TOKEN_MENU_OUT_FD"
	_github_token_menu_check "$token" || return 0
	if _github_token_menu_confirm "Save this token?"; then
		if github_token_write "$token"; then
			_github_token_menu_say "${C_GREEN:-}GitHub token saved.${C_RESET:-}"
		else
			_github_token_menu_say "${C_RED:-}GitHub token was not saved.${C_RESET:-}"
		fi
	fi
}

_github_token_menu_reveal() {
	local token=''
	github_token_read token
	if [[ -z "$token" ]]; then
		_github_token_menu_say "${C_YELLOW:-}No valid saved token is available to reveal.${C_RESET:-}"
		return 0
	fi
	printf '  %sWARNING: the full token will be printed once on this terminal.%s\n\n' \
		"${C_RED:-}" "${C_RESET:-}" >&"$GITHUB_TOKEN_MENU_OUT_FD"
	if _github_token_menu_confirm "Reveal the full token once?"; then
		# The secret gets space around it: it is the one line on this screen
		# the operator has to read off the terminal and type somewhere else.
		printf '\n  %s\n' "$token" >&"$GITHUB_TOKEN_MENU_OUT_FD"
		_github_token_menu_pause
	fi
}

# The saved token, checked against GitHub on demand. Saving checks what is
# being typed; nothing checked what was already there, and a token that was
# good when it was saved is exactly the thing that expires or gets revoked
# later. Same three outcomes as the save path, for the same reasons.
_github_token_menu_check_saved() {
	local token='' rc=0
	github_token_read token
	if [[ -z "$token" ]]; then
		_github_token_menu_say "${C_YELLOW:-}No valid saved token to check.${C_RESET:-}"
		return 0
	fi
	printf '  %sChecking the saved token with GitHub...%s\n' \
		"${C_DIM:-}" "${C_RESET:-}" >&"$GITHUB_TOKEN_MENU_OUT_FD"
	github_token_verify "$token" || rc=$?
	case "$rc" in
	0) _github_token_menu_say "${C_GREEN:-}GitHub accepted the saved token.${C_RESET:-}" ;;
	1) _github_token_menu_say "${C_RED:-}GitHub rejected the saved token; it is invalid, expired, or revoked.${C_RESET:-}" ;;
	*) _github_token_menu_say "${C_YELLOW:-}Could not reach GitHub to check the saved token.${C_RESET:-}" ;;
	esac
}

_github_token_menu_remove() {
	if [[ ! -e "$(github_token_file)" && ! -L "$(github_token_file)" ]]; then
		_github_token_menu_say "${C_DIM:-}No saved token file exists.${C_RESET:-}"
		return 0
	fi
	if _github_token_menu_confirm "Remove the saved token?"; then
		# Cleared first so a stale reason from an earlier attempt cannot be
		# reported against this one.
		GITHUB_TOKEN_REMOVE_REASON=''
		if github_token_remove; then
			_github_token_menu_say "${C_GREEN:-}Saved token removed.${C_RESET:-}"
		else
			_github_token_menu_say "${C_RED:-}Not removed: ${GITHUB_TOKEN_REMOVE_REASON:-the saved token could not be removed safely}.${C_RESET:-}"
		fi
	fi
}

github_token_menu() {
	local action=''
	_github_token_menu_open_fds || return 1
	_github_token_warning_scope_begin
	_GITHUB_TOKEN_MENU_STATUS=''
	while true; do
		ui_clear
		_github_token_menu_render
		# Shown once: it describes what just happened, not what is true.
		_GITHUB_TOKEN_MENU_STATUS=''
		_github_token_menu_line action "  ${C_BOLD:-}Select action:${C_RESET:-} "
		# One blank below the answer, so an action's output starts on its own
		# rather than running straight on from the line it was asked on. Here
		# rather than in each action: every one of them wants it.
		printf '\n' >&"$GITHUB_TOKEN_MENU_OUT_FD"
		case "$action" in
		s | S) _github_token_menu_save ;;
		r | R) _github_token_menu_reveal ;;
		c | C) _github_token_menu_check_saved ;;
		d | D) _github_token_menu_remove ;;
		q | Q) break ;;
		*) _github_token_menu_say "${C_YELLOW:-}Invalid choice.${C_RESET:-}" ;;
		esac
	done
	_github_token_warning_scope_end
	_github_token_menu_close_fds
}
