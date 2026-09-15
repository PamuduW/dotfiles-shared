# dotfiles-shared

The one copy of the code that the [`dotfiles`](https://github.com/PamuduW/dotfiles)
installer and the [`agentbot`](https://github.com/PamuduW/agentbot) CLI both
run. Neither repository vendors it any more; both resolve this checkout at
runtime.

## What lives here

| Path | Contents |
|---|---|
| `scripts/lib/shared/tui/` | The Bash renderer: colours, menus, tables, TTY handling |
| `scripts/lib/shared/python/` | The Python layout module and probe/report helpers |
| `scripts/lib/shared/*.sh` | Token storage, repository update, askpass, sudo priming |
| `tests/lib/shared/assert.sh` | The assertion helpers both test suites use |

The paths are deliberately the ones the consumers already used, so each
consumer resolves a root and appends the same relative path it always did.

## How consumers find this checkout

Resolution order, first match wins:

1. `DOTFILES_SHARED_DIR`, if set
2. a sibling of the consuming repository (`../dotfiles-shared`)
3. `$HOME/dotfiles-shared`

If none resolve, the consumer stops with the clone command rather than a bare
`source: No such file` from whichever line happened to load first.

## CONTRACT

`CONTRACT` holds a single integer. Each consumer declares the version it
requires and checks it during preflight. Raise it when a change here is not
backward compatible with a released consumer, and update both consumers in the
same batch.

A mismatch is reported with the required and found versions, so version skew
across the three repositories fails loudly instead of drifting.

## Changing shared code

This repository is canonical. There is no sync step and no vendored second
copy to keep aligned — edit here, then run both consumers' suites, which
exercise this code through their own parity tests.
