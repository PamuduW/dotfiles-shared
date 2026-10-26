# Software catalog

`software.json` is the canonical inventory and version data. The initial
inventory is **unqualified**: it contains no approved release and must not be
used to claim that a version was installed or tested. `auto_promote` remains
false until all required platforms and component checks are qualified.

Base OS packages are distro-managed. Vendor applications installed through apt
are separate catalog-controlled components. Configuration actions, groups and
hosted services do not receive invented software version numbers.

The product-owned inventory checker enumerates current registries, apt package
names, requirements, selected skill sources, MCP integrations and VS Code
extensions. Missing, unexplained or conflicting identities fail coverage.

The JSON is authored here. `software.tsv` is generated for Bash consumers by
`scripts/render_catalog.py`; never edit the TSV directly. Approval is a separate
receipt produced only after real qualification. Discovery and fixture tests do
not approve this seed.

Qualification currently targets **Ubuntu 24.04 Linux AMD64** (`ubuntu-24.04`),
as selected by the operator on 2026-10-26. ARM64 and WSL2/Windows behavior are
outside this rollout's compatibility claim. Windows-only extensions remain in
the inventory, are preserved during discovery, and appear in the readiness
report's `excluded_components`. They are not approved by a Linux receipt.

Cursor CLI is an operator-approved `external` exception. It remains inventoried,
receives no approved artifact, and is listed under `manual_components` in status.
Normal and forced catalog installs and updates skip it; status reports the
installed version. The catalog does not control its automatic updater.

Python candidates use the frozen Agentbot `requirements.txt` and
`requirements-dev.txt` constraints. Explicitly selected uv resolves public wheel
metadata into hash-pinned runtime and development closures for Python 3.12 on
Linux AMD64. Runtime versions are constrained to the development solution, while
development-only packages stay out of runtime. Discovery never installs these
packages; real qualification remains required before approval.

When no WSL-specific artifact exists, readers can reuse the Linux artifact for
the same CPU architecture. This does not certify Windows integration. Agentbot
leaves Windows extension installation under operator control during this rollout.

VSIX locks record known built-in editor dependencies separately from Marketplace
packages. Before installation, Agentbot checks their manifests in the selected
editor. The editor remains an external prerequisite; these checks do not install
or upgrade it. An unknown or missing built-in dependency blocks installation.

`Catalog candidate discovery` is a read-only daily workflow on `ubuntu-24.04`.
It uploads a complete, unapproved proposal with its source identities. The manual
`seed` input prepares initial tooling when no approved catalog exists. Scheduled
runs require an approved baseline. A proposal artifact is not full installation qualification or permission to
publish. After discovery, a private-home smoke test installs the selected
Node/npm/uv tooling, then Agentbot's Python wheel closure and Skills CLI npm
closure. It checks installed Python distribution versions, exact physical Node/npm
versions, CLI startup and repeat-install reuse. Its separate `tooling-smoke`
artifact covers this subset only. It cannot satisfy the full inventory, upgrade
or failure-recovery gates. The full installation controller and protected
publisher are still required before enabling catalog rollout.

While the candidate workflow exists only on development, start its read-only
seed run through the existing workflow:
`gh workflow run validate.yml --repo PamuduW/dotfiles-shared --ref development -f mode=seed`.
The default `checks` mode still runs the regression gates. `proposal` mode needs
an approved baseline. These modes share the candidate implementation.

A separate clean Ubuntu 24.04 container extends smoke coverage to Dotfiles'
user-local software. Selection comes from the frozen inventory; deb/OCI artifacts
and fonts remain outside that driver. It installs each selected tool, verifies
ownership and the physical version, then repeats the install. Sources are mounted
read-only, the container runs as a non-root user with memory/CPU/process limits,
and no Docker socket or credentials are mounted. A pinned Trivy runs a container
filesystem scan and records findings; the scan report does not certify an absence
of vulnerabilities. This driver and its separate `user-tools-smoke` artifact
remain partial evidence, not full qualification or permission to publish.
