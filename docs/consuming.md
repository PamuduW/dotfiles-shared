# Consuming this repository

Both products resolve this checkout at runtime. Neither vendors a copy, and
there is no sync step.

## Resolution

First match wins:

1. `DOTFILES_SHARED_DIR`, if set
2. a sibling of the consuming repository (`../dotfiles-shared`)
3. `$HOME/dotfiles-shared`

If none resolve, the consumer stops and prints the checkout it wanted and the
`git clone` that creates it. It does not fall through to a bare
`source: No such file` from whichever line happened to load first — which is
exactly what `agentbot/install.sh` would do, since it loads shared code on its
very first `source`.

Resolution happens twice, once per language:

| Consumer side | File | Publishes |
|---|---|---|
| Bash | `scripts/lib/shared_resolve.sh` (in each consumer) | `DOTFILES_SHARED_ROOT`, `DOTFILES_SHARED_LIB` |
| Python | `agentbot/src/shared_paths.py` | `shared_root()`, `shared_python_path()` |

`agentbot/tests/check_shared_contract.sh` fails if the two ever disagree about
the revision they require.

Both resolvers use only builtins and the standard library. An earlier version
used `dirname` and `tr`, and an isolated-`HOME` test with a stripped `PATH`
reported the resulting empty read as an unreadable `CONTRACT`.

## CONTRACT

`CONTRACT` holds one integer. Each consumer declares the revision it requires
and asserts it during preflight; a mismatch names both revisions and the pull
that fixes it.

**Raise it whenever the pairing changes**, not only when something here is
removed or altered. Adding a file looks additive from this side, but the moment
a consumer loads it that consumer no longer works against an older checkout.
Three files were added before this was understood, each committed as "additive,
so the CONTRACT revision is unchanged"; pairing a current consumer with the
previous revision then died on `repo_gate.sh: No such file or directory`.

Raise it in the same batch, in three places:

| Where | What |
|---|---|
| `CONTRACT` | the integer |
| `scripts/lib/shared_resolve.sh` | `DOTFILES_SHARED_CONTRACT_REQUIRED`, in **both** consumers |
| `agentbot/src/shared_paths.py` | `CONTRACT_REQUIRED` |

## The binding pattern

Code here that differs per product takes a binding: the product sets what
differs, then calls the shared entry point. It does not branch on which product
is running.

| Shared | Binding supplies |
|---|---|
| `repo_update.sh` | recovery prefix, report breadcrumb, result vocabulary |
| `repo_gate.sh` | the runner that gates one repository, and the target order |
| `github_token_menu.sh` | breadcrumb root, width source, whether a TTY seam needs refreshing |

Each consumer's binding lives at the path it always used, so callers did not
change: `scripts/lib/repo_update.sh`, `scripts/menus/github_token.sh`.

## Changing shared code

This repository is canonical — edit here, not in a consumer. Then run both
suites, which exercise everything here through their own parity tests:

```bash
bash ../agentbot/tests/run.sh
bash ../dotfiles/scripts/validate.sh
```

`scripts/validate.sh` is the Dotfiles **full** gate; `tests/run.sh` there runs
the test files only.

CI checks out this repository beside each consumer, so a runner is laid out the
way a real machine is and exercises the same resolution an operator gets.
