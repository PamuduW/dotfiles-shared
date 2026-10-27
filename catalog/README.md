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

The Linux font smoke driver separately checks fresh/repeat installation,
fontconfig registration and matching of the installed Ubuntu Sans Mono family.
The behavior driver executes a Node program, compiles a Go program with network
module resolution disabled, checks headless Chrome's DOM output, and renders a
Mermaid SVG using the selected browser. These are representative checks, not
complete feature coverage. Their separate receipts remain unapproved; Windows
fonts remain outside Linux qualification. Each new driver requires successful hosted execution before its
results count as evidence.

The vendor-package driver installs the selected deb packages twice in a separate
container. It checks package ownership, exact versions and apt holds, runs a local
PowerShell pipeline and checks for a visible Obsidian window under Xvfb. It does
not start Docker or certify Portainer.

The integration driver uses a checksum-verified full Linux VS Code desktop client
as an external test prerequisite. It applies the selected Linux extension closures
and settings twice through normal Agentbot operations, checks owned versions, and
verifies pinned skill-source selection, copied content and provenance on repeat.
It does not run inference or authenticate agent clients. The standalone server
archive omits client-side language extensions required by some selected packages.

Agentbot's `python_runtime` component selects the relocatable CPython 3.12 ABI
used by the Python locks. Discovery freezes the publisher's exact build date,
version, archive URL and SHA-256. Agentbot installs it under private application
data, verifies payload ownership/integrity and physical runtime identity, and
uses it for both runtime and development environments. OS Python packages stay
distro-managed. The tooling receipt and inventory audit bind this SDK to the
same candidate; changing its identity rebuilds the environment. Agentbot's
private Node/npm dependency similarly uses the qualified Dotfiles installer in
an isolated home, without requiring public Node or NVM ownership.

The tooling driver also installs the complete frozen Python development closure
in a separate environment, checks exact distributions after fresh/repeat installs,
and runs Ruff, mypy and all Python test modules with those dependencies.

A final Docker lifecycle driver runs only with explicit disposable-host opt-in
and GitHub-hosted Ubuntu guards. After the container checks finish, it removes the
runner's preinstalled Docker packages so they cannot satisfy fresh coverage, then
uses the normal selected Docker/Portainer installers. It checks the daemon, exact
image, local status API, repeat installation and synthetic volume-data survival
after an injected replacement failure. This driver must never run on a user's
machine. Each driver emits partial unapproved evidence; none supplies an approved
baseline upgrade or authorizes publication.

The final smoke audit requires all seven evidence scopes, identical candidate and
source identities, and fresh/repeat observations for every versioned Linux
component, including the NVM and asdf Go Git sources. It checks dependency closure
versions, extension ownership, skill provenance and the selected container image.
Its result explicitly leaves baseline upgrades, complete failure safety, all
updater controls and full product gates unqualified. The full approval validator
rejects this partial audit format.

`python3 scripts/catalog/publication-status.py REVIEWED-POLICY.json` is a
read-only GitHub evidence reader and publication planner. Its policy supplies the
exact repository/workflow/run/attempt, candidate and trusted reusable-workflow
commits, expected source/base heads, required jobs/steps and publisher App ID.
The policy must come from reviewed controller configuration, never an artifact.
The reader accepts only bounded GitHub GET responses and a checksum-verified
`receipt.json` ZIP envelope naming the same candidate, workflow, attempt and
approved predecessor. Partial smoke artifacts cannot authorize its plan.

The planner checks classic branch protection, current human review requirements,
strict App-owned status checks, no review/admin bypass, and no force pushes or
branch deletion. Personal repositories do not need organization-only push
restrictions. Organization repositories require the configured sole publisher
App. Ruleset-based protection needs a separate verifier and currently holds.
The repository must already have an approved predecessor and automatic promotion
enabled through a reviewed change. A missing seed, protection or complete result
produces `status: held` and `published: false`.

A successful unit fixture produces an immutable plan containing only the two
approval proof files, bound to candidate content and live heads. It performs no
remote writes. The App writer, candidate preparer, trusted reusable qualification
workflow and complete baseline-upgrade evidence are still required; this command
cannot publish or enable the current unapproved seed.
