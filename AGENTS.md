# AGENTS.md

## Project

This repository is the single source for code shared by the `dotfiles`
installer and the `agentbot` CLI: the Bash renderer, the Python layout module,
token storage, repository update, and the shared test assertions. It was
extracted from `dotfiles`, which vendored it to `agentbot` through a sync
script until that mechanism was retired.

## Working rules

- This repository is canonical. Nothing here is generated, and no consumer
  carries a second copy to reconcile.
- Keep the existing paths under `scripts/lib/shared/` and `tests/lib/shared/`.
  Consumers resolve a root and append these exact relative paths; moving a file
  breaks both of them.
- The Bash renderer and the Python layout module must agree byte for byte. They
  are pinned by `agentbot/tests/test_renderer_parity.sh` across seven terminal
  widths and by `dotfiles/tests/test_probe_classify_parity.sh`. Change them
  together and run both suites.
- Raise `CONTRACT` when a change is not backward compatible with a released
  consumer, and update both consumers in the same batch.
- This code runs during first setup, before either consumer has a Python
  environment. Do not add dependencies beyond Bash and the standard library.
- Do not add credentials, private paths, or host-specific values. This
  repository is public.

## Validation

There is no suite here. Shared code is exercised by its consumers:

```bash
bash ../agentbot/tests/run.sh
bash ../dotfiles/tests/run.sh
```

## Scope boundary

This repository owns shared implementation only. Component logic, menus
specific to one product, install flows, and agent policy belong to the
consuming repositories.
