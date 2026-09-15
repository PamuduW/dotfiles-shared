# Architecture

## What is here

| Path | Contents |
|---|---|
| `scripts/lib/shared/tui/` | The Bash renderer: colours, menus, tables, TTY handling |
| `scripts/lib/shared/python/` | The Python layout module, probe classification, and the report renderer |
| `scripts/lib/shared/github_token.sh` | Token storage, validation, and the GitHub check |
| `scripts/lib/shared/github_token_menu.sh` | The token screen both products show |
| `scripts/lib/shared/repo_update.sh` | The repository update state machine |
| `scripts/lib/shared/repo_gate.sh` | Gating every repository a command depends on, in one pass |
| `scripts/lib/shared/py_service.sh` | One Python interpreter per command, as a coprocess |
| `scripts/lib/shared/{askpass,sudo_prime}.sh` | Masked password prompt and sudo priming |
| `tests/lib/shared/assert.sh` | Assertion helpers both suites use |
| `tests/lib/shared/lexicon.py` | The presentation contract's lexicon check |

## Why several things exist twice

The renderer, the colour vocabulary and the probe classification each exist in
**Bash and in Python**, and that is deliberate rather than debt.

First setup runs before a Python runtime exists: `python3` lives in the
optional `python` component, so an operator who deselects it still has to see
an install summary. The Bash half is the pre-runtime path and stays
permanently. Workspace ADR-0001 records the decision.

The two halves must agree byte for byte, which is what makes the duplication
safe to keep:

| Pair | Pinned by |
|---|---|
| `tui/report_table.sh` ↔ `python/report_table.py` | `agentbot/tests/test_renderer_parity.sh`, seven terminal widths |
| `tui/colors.sh` ↔ `python/render_report.py` | the same suite, every state either vocabulary knows |
| `tui/menu_*.sh` ↔ `agentbot/src/ui/` | `agentbot/tests/test_menu_parity.sh` |
| `python/probe_classify.py` | `dotfiles/tests/test_probe_classify_parity.sh` |

Change one half without the other and a parity suite fails. That is the point:
the column boundaries of the two renderers once disagreed by one place, and a
`dotfiles full-update` printed two tables that did not line up.

## What is deliberately not here

- **Component logic, install flows, and product-specific menus.** Those belong
  to the consuming repository.
- **The resolver.** `scripts/lib/shared_resolve.sh` and
  `agentbot/src/shared_paths.py` live in the consumers, in duplicate, because
  they are what *finds* this repository. They use only Bash builtins and the
  standard library: they run before a run has proven anything about `PATH`.
