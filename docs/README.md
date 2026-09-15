# dotfiles-shared documentation

This repository is implementation only. It has no CLI, no install, and no test
suite of its own — everything here runs inside one of its two consumers, and is
exercised by their suites.

| Document | Covers |
|---|---|
| [Architecture](architecture.md) | What lives here, why each piece is shared, and the two-language split |
| [Consuming it](consuming.md) | How a product resolves this checkout, the binding pattern, and CONTRACT |

The root [`README.md`](../README.md) is the short version; [`AGENTS.md`](../AGENTS.md)
is the working policy.

## Where the rules live

This repository holds code, not contracts. The rules its code implements are
written down in the workspace:

- **Presentation** — `docs/designs/presentation/README.md` in the workspace
  repository. Report shape, section rules, colour meaning, the left edge, and
  the lexicon. Every rule names the test that holds it.
- **Why this repository exists** — workspace `docs/adr/0002-one-shared-library-repository.md`.
- **The language split** — workspace `docs/adr/0001-python-above-a-thin-bash-bootstrap.md`.
  It is why several things here exist twice, in Bash and in Python.
