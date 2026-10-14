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

- **Presentation** — [`presentation.md`](presentation.md): report shape,
  section rules, colour meaning, the left edge, and the lexicon. Every rule
  names the test that holds it.
- **Why this repository exists** — Dotfiles and Agentbot load the same
  renderer, layout and readings at runtime, so they share one implementation
  instead of two copies that drift.
- **The language split** — Python wherever an interpreter exists, Bash for the
  bootstrap that runs before one does. It is why several things here exist
  twice, in Bash and in Python.
