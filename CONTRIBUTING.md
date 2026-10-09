---
title: "Contributing"
status: "current"
authoritative_source: "repository contribution, architecture, test, and review policy"
verified: "2026-08-09"
audience: "human contributors"
maintenance: "hand-maintained"
concern: "contributor-contract"
---

# Contributing

Thank you for helping improve Quorune. This document is the human
contributor contract: it explains which work the project supports, how to keep
changes reviewable, and what evidence maintainers need before merging them.

The project is public and experimental. Its primary supported profile is
four-player Free-for-All Commander, with bounded support for other documented
profiles. It is not a complete implementation of the Comprehensive Rules, has
not received an independent security audit, and is not ready for unreviewed
public deployment. Check the generated [platform](docs/PLATFORM_IMPLEMENTATION_STATUS.md),
[rules](docs/RULES_COMPLETENESS_STATUS.md), and
[compiler](docs/COMPILER_COVERAGE_STATUS.md) status before relying on a
capability or selecting work.

## Sources of authority

When sources disagree, use this order:

1. implemented code, versioned schemas, and executable tests;
2. machine-readable policy, source manifests, and pinned snapshots;
3. generated reports produced from those inputs;
4. current hand-maintained documentation;
5. ADRs and the changelog as historical explanations.

[`rules/manifest.json`](rules/manifest.json) identifies the pinned
Comprehensive Rules and card-data snapshot used by generated evidence. The
worktree's configured SQLite database owns its exact Oracle and rulings
content. Do not silently substitute current web text, memory, or a different
Scryfall export. Generated totals and fingerprints belong in generated reports,
not this document or a pull-request narrative.

## Choose and scope work

Search existing issues and pull requests before starting. Prefer an existing,
well-bounded issue; otherwise open the relevant issue form with a sanitized
reproduction and the governing source. Coordinate before beginning a protocol,
persistence, replay, schema, privacy, or broad rules change.

Card-support pull requests deliver at least 200 net newly capability-closed
Commander cards from the pinned snapshot. Combine several measured mechanics
in one delivery pull request when needed. Keep each mechanic scoped to its
existing typed owners, with independently reviewable commits, behavior
contracts, tests and unsupported boundaries. Count distinct Oracle IDs in the
final combined corpus, subtract support losses, and preserve complete
cross-mechanic interaction evidence. Per-family probe thresholds help select
work; they do not authorize merging a smaller support delivery.

Include implementation, removal of superseded paths, focused evidence,
generated artifacts and the documentation needed for the delivery. Split
unrelated cleanup, formatting and opportunistic refactors. Correctness-first
work retains its existing priority. Avoid card-specific patches and broad
rewrites without reviewed ownership boundaries.

Branch names should use a short category and outcome, such as `fix/`,
`rules/`, `docs/`, `test/`, or `chore/`. Start from the requested base and do
not reuse a worktree that contains another change. Commits should be focused
and explain the durable outcome. Every commit subject and pull-request title
must use the Conventional Commit form `<type>: <imperative subject>`, for
example `fix: preserve replay ordering`. Do not merge, rebase, force-push, or
rewrite another contributor's branch without explicit coordination.

## Set up the local environment

Use CPython 3.12.x exactly. Python 3.11 and 3.13 or newer are unsupported. On
Windows, run `scripts/bootstrap_windows.ps1`, or create the environment
directly:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts\worktree_bootstrap.py --install-hook `
  --db "C:\path\to\the\pinned\scryfall-current.sqlite3"
.\.venv\Scripts\python.exe scripts\build_test_database.py build `
  --fixture tests/fixtures/scryfall-exact-lists.json `
  --output data/test-ci.sqlite3
npm ci --prefix web
npm run generate:types --prefix web
npm run typecheck --prefix web
npm run build --prefix web
```

Set `MTG_CARD_DB=data/test-ci.sqlite3` only while running focused tests that
require compact card data. Restore the pinned database value or remove that
environment override before generated finalization and push. Keep environments,
databases, downloaded snapshots, caches, and build output in ignored paths.

The readiness command accepts the database explicitly with `--db`, from
`MTG_CARD_DB`, or at `data/scryfall-current.sqlite3`. It fails differently for
a missing database, an unreadable database, and a database whose metadata does
not match the tracked compiler-corpus snapshot. It also verifies test-shard
ownership and prints the correct generated-finalization command. The compact
test database created above is intentionally not accepted as the pinned corpus
database.

## Test the change

Start with the smallest regression that proves the defect or missing behavior.
Run that test red before the implementation when practical, then green at the
final diff. Run the affected focused module and directly relevant interaction
tests. Inspect the deterministic local quick-gate plan before executing it:

```powershell
.\.venv\Scripts\python.exe scripts/quick_gate.py --dry-run
.\.venv\Scripts\python.exe scripts/quick_gate.py
```

The evidence must match the risk of the change. Record `N/A` with a reason when
a class truly does not apply.

- Rules and engine changes need positive, negative, malformed-input, rollback,
  multiplayer/APNAP, offer-versus-command parity, replay, privacy, bounded
  property, and focused mutation evidence where applicable.
- Replay or persistence changes need byte, canonical JSON, hash, save/load,
  compatibility, and exact command-replay evidence.
- Projection or identity changes need adversarial seat-privacy and capability
  tests. Never put private hands, library order, checkpoints, credentials, or
  live records in fixtures or reports.
- Browser, room, authentication, WebSocket, reconnect, or visible-state changes
  need isolated headless browser evidence. Automated work must not open or
  navigate a visible browser.
- Compiler and CardProgram changes need parser/lowering stage tests, source-span
  and residual assertions, typed construct validation, construction and
  capability-closure evidence, and corpus deltas against the pinned snapshot.
- Architecture changes need dependency, ownership, direct-write,
  card-identity-flow, Oracle-ID-literal, module-classification, and generated
  architecture checks.

Public exact-head pull-request CI is the ordinary merge authority. Do not
weaken, bypass, rename, or make required checks optional when the service is
unavailable. Hold behavioral, replay, browser, persistence, compiler, rules,
and schema changes until normal certification returns. See the
[CI pipeline guide](docs/development/ci-pipeline.md) for the complete workflow.

## Preserve architecture and rules ownership

`CommanderEngine` and its typed rules subsystems are authoritative. Clients,
pilots, transports, and generated programs propose actions; they do not mutate
zones, life, mana, the stack, counters, choices, or effects.

Rules work must follow the [dependency and mutation policy](docs/architecture/dependency-rules.md)
and accepted [ADRs](docs/adr/index.md):

- model a reusable Comprehensive Rules behavior with immutable typed queries,
  proposals, transactions, and a narrow declared mutation owner;
- share the same legality, cost, target, and capability path between advertised
  actions and accepted commands;
- fail closed for unknown Oracle semantics, unsupported grammar, malformed
  persisted state, and untrusted dependencies;
- lower Oracle text to source-spanned typed CardProgram constructs at build or
  compile time; never parse Oracle prose during a game transition;
- never add card-name, collector-number, set-code, or Oracle-ID behavior to the
  generic runtime;
- never write `GameState` directly outside its declared owners, or add a second
  registry, scheduler, compiler, or mutation path to avoid an existing owner;
- preserve deterministic hashes, transactional rollback, fixed-seat privacy,
  protocol versions, and the Game Record replay contract.

Do not move code solely to reduce a line count. A decomposition must transfer a
coherent behavior, remove the previous path, and narrow ownership or dependency
direction.

## Own generated artifacts

Machine-readable policy and source manifests own changing facts. Generator
scripts own generated Markdown, JSON, compressed JSON, schemas, and browser
types. Never hand-edit an output to make a check pass.

`platform/generated-artifacts.json` is the ownership and dependency manifest
for the deterministic Python reports enforced by generated/architecture CI.
Protocol types, pinned rules snapshots, and other separately governed generated
assets retain their existing owners. Do not guess individual report-generator
order. After source, tests, and documentation form a coherent worktree, and
before the final commit, run:

```powershell
.\.venv\Scripts\python.exe scripts\finalize_generated.py --write
```

The writer repeats changed generators and downstream consumers to a fixed
point, then verifies every tracked output, documentation policy, and diff
hygiene. Pass `--db <path>` or set
`MTG_CARD_DB` when compiler or corpus changes require the pinned card database.
Manual benchmark baselines remain explicit. Inspect and stage every resulting
output with its source before the final commit. The repository-owned pre-push
hook is a backstop: it performs the same finalization and aborts when it creates
uncommitted files; it never changes commit history automatically.

## Update documentation deliberately

Follow the [documentation map and standard](docs/index.md). Update the one
current document that owns the concern and remove superseded guidance in the
same pull request. Current prose uses present tense and links to generated
status instead of copying changing counts, branch names, run IDs, or milestones.

Create an ADR only for a durable architecture decision whose alternatives and
consequences will matter after the implementation changes. Do not rewrite an
accepted ADR; supersede it with a new decision. Historical narrative belongs
only in ADRs, [CHANGELOG.md](CHANGELOG.md), and `docs/history/`.

Documentation changes must pass:

```powershell
.\.venv\Scripts\python.exe scripts\finalize_generated.py --write
```

## Respect content, privacy, and security boundaries

Contributions must comply with the [legal and third-party content boundary](docs/LEGAL_CONTENT_BOUNDARY.md).
Do not add Scryfall bulk archives, full Oracle exports, Comprehensive Rules
prose, card scans, artwork, official frames, set symbols, Wizards branding, or
private deck/game data. Small public facts in deterministic fixtures must be
necessary for the test and properly attributed.

Do not commit live Game Records, checkpoints, hidden zones, library order, raw
capabilities, guest tokens, invite codes, provider memory or reasoning,
credentials, SQLite databases, image/deck caches, or retained downloads. Use
temporary directories and sanitized initial-state recipes.

Report vulnerabilities privately as directed by [SECURITY.md](SECURITY.md).
Do not disclose a security defect or sensitive reproduction in a public issue
or pull request. Community-conduct reports follow the private process in the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Open a pull request

Use the pull-request template and make the description reviewable without
requiring archaeology. Identify the change class, supported profile and source
authority; ownership before and after; duplicate paths removed; runtime,
direct-write, prohibited identity-dispatch, and Oracle-ID literal deltas;
compiler and CardProgram effects;
tests by class; generated artifacts; documentation or ADR changes; exact
remaining limitations; privacy and rollback considerations; and rollback plan.
Use `N/A` with a concrete reason for sections that do not apply, especially for
documentation-only changes.

Keep the branch coherent and wait for normal required exact-head checks. Resolve
review comments in focused commits, then regenerate affected evidence at the
final head if the implementation changed.

Automated coding agents have additional operational guardrails in
[`AGENTS.md`](AGENTS.md). Human contributors are welcome to read them, but they
are not required human onboarding.
