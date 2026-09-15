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
requires and checks during preflight that this checkout is **at least** that.

At least, not exactly. A raise means a consumer started needing something this
tree gained, so a shared checkout *ahead* of a consumer is a superset and is
accepted. *Behind* is the failing direction: the files that consumer loads are
not here yet, and it stops with both revisions and the pull that fixes it.

Exact matching deadlocked a self-update. A consumer that predated a raise could
not start, so it could not run the gate that would have pulled the newer
consumer — and the error told the operator to pull the shared checkout, which
was already current.

**Raise it whenever the pairing changes, not only when something here is
removed or altered.** Adding a file looks additive from this side, but the
moment a consumer starts loading it, that consumer no longer works against an
older checkout — and the failure is a bare `No such file` from whichever line
sourced it, which is exactly what CONTRACT exists to replace.

Raise it here and in both consumers in the same batch:

| Where | What |
|---|---|
| `CONTRACT` | the integer itself |
| `scripts/lib/shared_resolve.sh` | `DOTFILES_SHARED_CONTRACT_REQUIRED`, in both consumers |
| `agentbot/src/shared_paths.py` | `CONTRACT_REQUIRED` |

## Documentation

[`docs/`](docs/README.md) covers the architecture, why several things exist in
both Bash and Python, and the full consuming contract.

## Changing shared code

This repository is canonical. There is no sync step and no vendored second
copy to keep aligned — edit here, then run both consumers' suites, which
exercise this code through their own parity tests.
