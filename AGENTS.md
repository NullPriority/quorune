---
title: "Codex project instructions"
status: "current"
authoritative_source: "repository contribution, architecture, and documentation policy"
verified: "2026-08-09"
audience: "Codex agents and contributors"
maintenance: "hand-maintained"
---

# Codex project instructions

Read this file completely before changing the repository. These instructions
are durable guardrails, not a status report. Never add branch names, pull
request numbers, CI run IDs, test totals or transient task notes here.

## Find current context

1. Inspect Git, worktree, pull-request and CI state instead of trusting a prior
   handoff.
2. Read `docs/index.md` and use its task routing table.
3. Read the relevant generated status report before choosing rules or
   architecture work.
4. Read only the architecture, reference, operations and ADR documents required
   by the task, then inspect their authoritative code and tests.
5. Treat implementation, schemas, machine-readable policy and executable tests
   as current behavior. Generated reports own changing measurements. ADRs and
   the changelog explain history; they do not override current behavior.

Useful status entry points:

- `docs/PLATFORM_IMPLEMENTATION_STATUS.md`
- `docs/RULES_COMPLETENESS_STATUS.md`
- `docs/COMPILER_COVERAGE_STATUS.md`
- `docs/ARCHITECTURE_DEBT_STATUS.md`
- `docs/REBRAND_STATUS.md`
- `coverage/card-unlock-frontier.md`
- `coverage/reusable-piece-matrix.md`

## Authority and safety boundaries

- Quorune is the first-party product and repository identity. Magic: The
  Gathering, Commander, Oracle, Comprehensive Rules, Scryfall, and Moxfield are
  third-party rules, format, or data compatibility references; do not replace
  those terms when they are technically accurate.

- Before renaming a distribution, module, environment variable, schema ID,
  protocol key, record field, replay prefix, command, or optional-client path,
  read `docs/REBRAND_STATUS.md`. Compatibility
  identifiers require an explicit migration rather than a global replacement.

- `CommanderEngine` and its typed rules subsystems are authoritative. A client
  never mutates zones, life, mana, stack, counters, choices or effects.

- Every player command uses an unconsumed capability issued to the authenticated
  principal. `principal` is transport identity, never client-selected data.

- Only a scoped arbiter capability can submit generic effects. Product gameplay,
  rules enforcement, CI and releases cannot depend on an LLM or live AI ruling.

- Project hidden information by principal. Never solve a UI or test problem by
  exposing an authoritative checkpoint, library order or another seat's data.

- Use the pinned local Scryfall snapshot during games. Network access belongs to
  managed data refresh outside game transitions.

- Material unknown Oracle semantics, unsupported grammar and untrusted
  capability dependencies fail closed before mutation.

- A yield is an optimization, never authority to suppress a changed meaningful
  action. `suppressed_meaningful_windows` must remain zero.

- Advertised actions and accepted commands consume the same typed legality,
  cost and target authority.

- Preserve protocol versioning, deterministic hashes, transactional rollback,
  principal projection and exact Game Record v3 replay.

- Do not infer provider/model identity, completion, rules fidelity or matchup
  evidence from partial or duplicated fixtures.

## Rules and architecture changes

The repository is incrementally extracting coherent rules ownership from the
central engine. Do not perform a big-bang rewrite and do not move code merely to
reduce a line count.

A valid rules family:

- represents a reusable Comprehensive Rules behavior rather than a card name,
  collector number, set code or Oracle ID;
- uses immutable typed queries/proposals/transactions and a narrow mutation
  owner;
- removes the prior implementation and narrows dependency direction;
- lowers source-spanned CardProgram V2 nodes when Oracle text participates;
- declares fine-grained capabilities and ambient interaction dependencies;
- shares legality between offers and command validation;
- fails closed for unsupported variants;
- adds positive, negative, malformed-input, rollback, multiplayer, replay,
  privacy, property and focused mutation evidence where applicable;
- regenerates rules, compiler, card, architecture and status artifacts once at
  the final exact head.

Do not add a second capability, mechanic, compiler, scheduler or runtime
component registry. Do not add runtime Oracle parsing or arbitrary executable
callbacks. Repeated source-pinned descriptors must become a generic compiler
production and component family.

Follow `docs/architecture/dependency-rules.md`
and the accepted [ADRs](docs/adr/index.md). A production module or function over
the policy threshold is measured debt; growth requires the documented review
path. The generated architecture audit is the measurement authority.

Before an ownership extraction, use `simctl architecture changed --base
origin/main`, `simctl architecture show <subsystem>`, and `simctl architecture
debt` to retrieve bounded current context. Use `writes`, `runtime-text`, and
`owners` for exact mutation, raw-text, and live worktree provenance. Do not
infer current branch, certification, or Slot A/Slot B state from generated
prose, and do not hand-edit architecture metrics. A new prohibited runtime
Oracle-text identity, fixed card identity selecting generic behavior,
engine-local direct write, or unowned direct write must fail the architecture
guard; removal is the intended direction. Card identity may remain typed data;
the bounded identity-flow inventory, not lexical collision with a card-name
corpus, determines whether it became implementation authority.

## Browser ownership

Every visible browser—including the Codex in-app browser—is user-owned state.
Do not open, reuse, focus or navigate one unless the user explicitly requests
visible interaction in the current task.

- Automated server checks use
  `.\.venv\Scripts\python.exe -m server --no-open`.
- Probe HTTP endpoints with CLI clients.
- Run UI checks only in isolated headless Playwright contexts.
- Keep Vite `open: false` and HTML reporters at `open: "never"`.
- Stop processes started for a check when the check ends.

An open browser, prior permission or running localhost listener is not current
authorization.

## Development and certification

Use the worktree-local CPython 3.12 environment, never a global `python` alias.
Keep one substantive branch under certification and at most one independent
next-batch worktree. Do not mix their changes.

Keep the filesystem equally bounded. The normal layout is the canonical
`C:\Code Projects\Quorune` checkout plus, only while Slot B is active, one
`C:\Code Projects\Quorune-<batch>` worktree. Before creating that second
worktree, remove any stale registered worktree after verifying it is clean and
contains no unpublished work. As soon as a branch merges or is abandoned,
remove its checkout with `git worktree remove`, run `git worktree prune`, and
retain the branch itself only when it still protects unique work. Never leave
one top-level project directory per historical pull request.

After creating or entering a worktree, run its repository-owned readiness
command. `--install-hook` may update only this repository's local Git hook
configuration and refuses to replace a foreign hook policy:

```powershell
.\.venv\Scripts\python.exe scripts\worktree_bootstrap.py --install-hook
```

The command verifies the exact CPython runtime, tracked pre-push hook, primary
test-shard ownership, and the pinned card database selected by `--db`,
`MTG_CARD_DB`, or `data/scryfall-current.sqlite3`, in that order. It compares
database metadata with the tracked compiler-corpus snapshot, reports missing,
stale, and invalid databases separately, and prints the exact standard and
database-backed finalizer arguments for the detected environment. Run it
without `--install-hook` for a read-only recheck. A compact test database is
not a substitute for the pinned corpus database.

As the default development policy, do not run broad behavioral suites, broad
gates, or historical regression journeys locally. During implementation, run
the exact new behavioral tests and the smallest directly affected owner or
interaction tests that cover the changed contract, together with changed-module
compilation, JSON/schema parsing, applicable deterministic generators and
freshness checks, and diff hygiene. Keep that focused behavioral set within a
small time budget; if the impact plan identifies a broader package, operating-
system, browser, or regression set, leave that set to public CI. Push the
coherent head so public CI runs the remaining affected regressions, replay and
privacy shards, the broad suite, packaging, and headless browser certification.
This is a limit on local test breadth, not a prohibition on behavioral tests;
new behavior requires its exact executable witnesses before the first cloud
checkpoint.
Inspect the deterministic impact plan without executing the broad gate:

```powershell
.\.venv\Scripts\python.exe scripts\quick_gate.py --dry-run
```

The dry-run output is a required change-impact inventory, not proof that the
identified work ran. Its `generated-finalization` step is the canonical
generated-output obligation. After source, tests, and documentation form a
coherent worktree—and **before the final commit**—satisfy the automatic
deterministic writers through either the local finalizer or the exact-source
cloud artifact workflow.

When a user-authorized focused check covers a semantic-handler or runtime-
component registration change, include
`CardProgramTrustTests.test_global_handler_and_component_inventory_is_capability_bound`
in that focused set. Family-level registration evidence does not replace the
global inventory ratchet.

```powershell
.\.venv\Scripts\python.exe scripts\finalize_generated.py --write
```

For a database-backed change that should be offloaded, push an explicitly
authorized source-checkpoint commit. The pull-request
`generated-artifacts.yml` run starts on source-changing events, executes the
pre-corpus sentinels, and publishes `cloud-generated-<sha>` for that exact ref.
It deliberately does not subscribe to `ready_for_review`; do not manually
restart it for a draft-to-review metadata transition. Download and install the
bundle with:

```powershell
$env:QUORUNE_CLOUD_SOURCE_CHECKPOINT_REASON = "seed exact-source cloud generation"
git push -u origin <branch>
Remove-Item Env:QUORUNE_CLOUD_SOURCE_CHECKPOINT_REASON
gh run download <run-id> --name cloud-generated-<source-sha> `
  --dir local\cloud-generated-download
.\.venv\Scripts\python.exe scripts\cloud_generated_artifacts.py install-bundle `
  --bundle-dir local\cloud-generated-download --expected-commit <source-sha>
```

The installer verifies the exact commit, source-tree fingerprint, manifest
inventory, and every output hash, then records an ordinary worktree-local
finalization receipt. Inspect and commit the installed outputs with their
source. The cloud run executes the same final freshness and architecture
checks; it does not authorize a source-only merge or a generated follow-up.
Owner artifacts are keyed by declared content inputs rather than commit SHA,
so a content-identical merge commit reuses the PR census and downstream work.
`main` pushes still publish a separately exact-main bundle without write
permissions. Use manual `workflow_dispatch` only for recovery or diagnosis.
The checkpoint environment variable is permitted only for this intermediate
feature-branch push; it fails on `main`, still runs shard/dependency and
compiler-identity sentinels, and is never used for the final generated-output
push.

Either route is the required focused architecture check before the final
commit.
The finalizer runs `scripts/validate_architecture.py --check` after generation,
so reviewed operation inventories, architecture exceptions, direct-write
ratchets, and module boundaries cannot be deferred to CI. Do not make the final
commit when this command fails. A successful write records a worktree-local
receipt in Git metadata. The tracked pre-push hook verifies that receipt and
falls back to the same complete finalizer when it is missing or stale.

When compiler, capability, CardProgram, or card-support behavior changes, run
the database-backed census in that same local finalization step or select the
cloud route above:

```powershell
.\.venv\Scripts\python.exe scripts\finalize_generated.py --write --db data\scryfall-current.sqlite3
```

`platform/generated-artifacts.json` owns the complete tracked generated-artifact
inventory, deterministic report commands, write policies, and dependency order.
Reusable owners also declare their direct input groups, implementation entry
points, execution class, database identity, and cache policy. Treat these as
correctness boundaries: update an owner declaration whenever its generator
starts reading another source or helper.
Its discovery policy recognizes the repository's generated path conventions,
generated-document metadata, pinned rules indexes, and embedded third-party
generator markers. Validation fails when a discovered artifact has no owner,
an output has two owners, an output escapes the repository, or a registered
output is absent, untracked, or lacks an independent discovery signal. Pinned
rules snapshots, browser protocol bindings, durable
baseline history, and the protocol demo retain their specialized/manual
generation workflows, but they still have exactly one manifest owner. Do not
hand-order individual platform-status, architecture-audit, or coverage writers,
and do not add a tracked generated artifact without registering its owner.
The registered `rules-derived` owner rebuilds conformance cases, the rules
manifest hashes, the mechanic registry, and their coverage documents whenever
an authoritative conformance review or mechanic contract changes. Do not run
`simctl rules sync`, hand-edit those outputs, or wait for `rules verify` in CI
to discover that drift; the ordinary finalizer command owns it.
`--write` repeats changed owners and their downstream automatic/derived writers
until a pass changes nothing, then runs every freshness check, documentation
validation, and diff hygiene. Database-backed corpus writers use `--db <path>`
or `MTG_CARD_DB`; omitting the database does not refresh those reports. The
tracked pre-push hook first accepts a current ordinary receipt because that
finalization already checks database-backed freshness. It also accepts a current
database-bound receipt by verifying the database path and fingerprint recorded
by that finalization, including when the database lives in the canonical
worktree. If the receipt is stale, the hook uses
`data/scryfall-current.sqlite3` when present and otherwise prints the required
database guidance. An explicit `MTG_CARD_DB` requires a receipt bound to that
exact database. Manual performance baselines are never rewritten implicitly.

Inspect every changed generated output, stage it with the source that caused
it, and make the final commit only after the command succeeds. Do not defer this
step until CI and do not repair platform status, architecture audit, and
reusable-piece fingerprints in separate follow-up commits. Use `--check` for
read-only diagnosis; do not run it immediately after a successful `--write`.

If a database-backed finalizer run reaches a later owner and fails, fix the
source error first. When that correction cannot affect earlier owners, resume
through the same canonical coordinator instead of rerunning the expensive
upstream corpus or hand-ordering generators:

```powershell
.\.venv\Scripts\python.exe scripts\finalize_generated.py --write `
  --db data\scryfall-current.sqlite3 --resume-from <generator-id>
```

Resume mode runs the named manifest owner and every descendant, then still
runs all registered freshness, architecture, documentation, and diff checks.
The ordinary first finalization and a stale-receipt pre-push fallback always
run the complete manifest. A current receipt skips only that redundant local
rerun; it is bound to the tracked source tree, every registered output, the
manifest discovery check, and the selected database file identity.
Add new source, test, and documentation files to the Git index before this
final run so the receipt fingerprints the files intended for the final commit.

The worktree readiness command installs the repository-owned pre-push hook.
The lower-level hook-only command remains available for repair:

```powershell
.\.venv\Scripts\python.exe scripts\install_dev_hooks.py
```

The repository intentionally uses a pre-push backstop instead of a pre-commit
writer: generated changes must be inspected and staged in the same deliberate
commit as their source, and some database-backed censuses are too expensive to
run on every intermediate commit. The command above and the required
pre-final-commit finalizer are therefore both part of worktree setup; do not
publish from a worktree whose `core.hooksPath` is not `.githooks`.

The hook is a backstop, not the normal finalization point. It uses only the
worktree-local CPython 3.12 environment. Before the corpus finalizer, it
validates that every discovered `tests/test_*.py` module has exactly one
primary shard, because a missing assignment prevents CI planning and skips the
entire matrix. It then accepts only an exact current local finalization receipt
or runs the full writer/check itself. That fallback may write missing generated
outputs, but it aborts the push so they can be inspected and committed; it
never amends or pushes a commit itself. Committing the already-finalized file
contents does not invalidate the receipt, while any later source, output,
manifest, database, or ownership change does. Hooks are advisory, so public
exact-head CI remains mandatory. Also
execute every other applicable
non-behavioral command identified by the plan, including changed-module
compilation, JSON/schema parsing, architecture or repository validators, and
diff hygiene. Resolve every omission before pushing the coherent head.

This authorizes only the exact new tests and smallest directly affected owner or
interaction witnesses before the first cloud checkpoint. It does not authorize
broad gates, historical regression journeys, package or operating-system
matrices, or browser certification. Those remain public exact-head CI
responsibilities except under the diagnostic and release-critical exceptions
below.

When adding or changing a fixture, manifest entry, registry record, generated
source, schema, workflow input, or package input, identify every consumer before
pushing. Do not assume that Linux, Windows, package, browser, or generated jobs
consume the same source unless the repository proves that they do. Prefer one
canonical manifest or machine-readable source. When duplicated consumer lists
must remain, add or preserve a deterministic completeness check.

The compact CI card database is specifically owned by
`tests/fixtures/compact-ci-fixtures.json`. Linux, Windows, generated, browser,
main-smoke, nightly, quick-gate, and local-gate consumers must call
`scripts/build_test_database.py build-ci --output <path>` and must not copy
`--fixture` arguments. Run `scripts/build_test_database.py validate-ci` after
changing its manifest, builder, or any consumer. Focused test-only databases may
continue to pass their narrow fixtures directly to `build_fixture_database`.

Compiler-only tests must construct a minimal `CardRecord` directly instead of
depending on an incidental card in the local or compact CI database. Tests of
generated inventories must compare identities against their authoritative
machine-readable inventory; never pin a volatile queue, capability, contract,
card, or residual total as an independent expected literal. Stable pinned-source
totals are allowed only when the count itself is the contract being tested.

When a compiler composition layer begins accepting complete syntax that leaf
compilers intentionally reject in isolation, audit the affected leaf compiler
negative suites before pushing. Preserve leaf parser rejection where it remains
correct, remove stale integrated residual expectations, and add every promoted
form to the composition owner's positive regression table. This cross-leaf
audit is part of the focused compiler check; do not wait for separate CI shards
to discover the promoted forms one at a time.

Push the coherent exact head and let public pull-request CI run the fail-closed
impact-selected Python, generated, package, platform and headless-browser
checks. Changes to selection, certification, test inventory, generated
ownership, broad state, replay, privacy, protocol or unknown paths retain the
complete pre-merge gate. The exact-main broad workflow executes the complete
cross-platform inventory after every merge without cancelling an older merge
SHA. Use that CI window for independent Slot B work instead of repeating the
same suite locally. Every commit subject and pull-request title must use the
Conventional Commit form `<type>: <imperative subject>`, for example `fix: preserve replay
ordering`; choose the type that describes the durable outcome. Complete
`.github/pull_request_template.md` before opening the pull request: remove its
instructional comments, fill every required stable review field and compact
evidence row, give a concrete reason for every N/A, and check every safety
assertion. The exact source head and generated base/head evidence are published
automatically in the `PR / Plan` summary; do not paste fingerprints, metric
tables, job conclusions or generated inventories into the description. When
generated inputs or outputs changed, the Generators run field must name
`scripts/finalize_generated.py --write`. Do not claim a broad local pass without
the exact command and numeric result, or a broad CI pass without the
authoritative GitHub Actions run URL. `PR / Plan` enforces source-event policy
on open, synchronize, and reopen. The separate `PR metadata / Plan` workflow
validates edits without creating a newer `PR / Certification` check. Moving a
draft to ready for review starts no workflow and preserves the exact-head checks
already produced for that unchanged pull request. Description edits never
launch, replace, cancel, or wait for a regression matrix. Keep the
description's scope, owner, exclusions, behavioral
evidence, limitations and rollback facts current, but do not edit it merely to
copy a new source SHA or change pending CI wording. The source event derives
its own exact base/head evidence from the immutable GitHub event.

Public CI has a hard 20-job concurrency envelope. Both the pull-request and
exact-main broad planners target at most 18 simultaneous jobs so cancellation,
certification, and incident recovery retain two slots. Do not add or widen a
matrix without updating and passing the corresponding checked concurrency
budget. Ordinary pull requests partition only the impact-selected module set
by its existing primary owners and add the compact `merge-core` overlay;
high-risk changes retain every primary module. Functional Ubuntu and Windows
shards use
four-process pytest-xdist execution with `loadfile` scheduling and exact
unittest collection parity; generated-governance remains on the sequential
unittest runner. The manifest owns one explicit observed-duration launch order.
Do not use automatic CPU counts, test-level distribution, unordered matrices,
or ad hoc parallelism. Preserve cross-platform shard result artifacts and
per-module timing telemetry so later balancing is based on observed durations.
Nightly runs the same primary partition on both operating systems with at most
six matrix jobs, verifies all OS/shard results in one fail-closed certification
job, and keeps five public-runner slots in reserve.

A successful `PR / Certification` publishes the ephemeral exact-head
receipt; Main smoke validates the squash-merged source tree against that
receipt. Never put PR numbers, branch names, exact heads, merge SHAs or workflow
run IDs into `platform/readiness-source.json` or
`platform/architecture-audit-source.json`. PR CI compares those durable sources
with the base revision and rejects newly written volatile provenance; the
historical CI escape ledger remains the explicit owner for observed workflow
incidents. Never create a follow-up commit solely to reconcile squash-merge
identity. Metrics run through a separate nonblocking completed-workflow
observer and never delay or invalidate the receipt. Main smoke then runs only
the merge-specific receipt/fingerprint check and compact deterministic
replay/server integration. `Main / Broad regression` preserves the complete
Ubuntu, Windows, package, generated, interaction and browser inventory for
every exact main SHA. A completed red broad run blocks later automatic merges;
only a labeled high-risk fix-forward PR may bypass that red-state query. A
broader local behavioral gate is exceptional: use it only when the
user asks or when diagnosing a specific CI-only or release-critical failure
that cannot be isolated from the Actions evidence. The required exact new and
directly affected tests remain the normal pre-cloud evidence; any exceptional
diagnostic must still run only the directly relevant test. Browser automation
remains headless. The complete workflow and recovery commands are in
`docs/development/ci-pipeline.md`.

Never stage `run/`, `local/`, SQLite databases, Scryfall archives, image or deck
caches, raw capabilities, private packets, provider memory or live Game Records.
Use temporary directories and sanitized recipes for regression records.

## CI failure triage and recurrence prevention

When public pull-request CI fails, inspect every failed job for the same exact
head before changing the branch. For each failed job, identify:

- the failed step;
- the exact command;
- the first actionable error;
- the source or tracked artifact involved.

Group failed jobs by shared root cause. Do not assume that every red job is an
independent defect. One omitted generator, stale artifact, missing fixture or
registry consumer, schema change, package input, or documentation update may
surface in several jobs.

Classify each root cause as one of:

- implementation or rules correctness;
- omitted deterministic repository command;
- stale generated or status artifact;
- missing documentation update;
- missing fixture, manifest, registry, or consumer update;
- package, platform, server, protocol, or browser integration;
- demonstrated transient infrastructure failure.

For a deterministic omission:

1. Use the Actions evidence to identify the authoritative source and every
   affected consumer.
2. Fix every manifestation of the shared cause in one coherent branch
   correction. Do not patch only the first failed job.
3. Run only the directly relevant local diagnostic permitted by the development
   policy above.
4. Rerun applicable compilation and parsing, then use
   `scripts/finalize_generated.py --write`; run any remaining architecture,
   repository, or platform-specific validators.
5. When the dry-run impact map or an existing validator reasonably should have
   identified the obligation, update that map or validator in the same branch.
6. When duplicated lists, copied workflow arguments, or independently maintained
   registrations caused the omission, replace them with one canonical source or
   add a deterministic completeness validator.
7. Push one corrected exact head, then return to independent next-batch work
   while public CI reruns.

Do not blindly rerun a deterministic failure against an unchanged head. A rerun
without a source change is appropriate only for a demonstrated transient
infrastructure failure.

Do not add a prose-only checklist item when the repository can mechanically
derive, validate, or centralize the obligation. Do not weaken or bypass a real
check merely because the immediate implementation appears correct.

## Documentation contract

This repository uses a docs-as-code adaptation of Diátaxis:

- tutorials teach a safe first success;
- how-to guides solve a concrete operator or contributor task;
- reference pages state precise interfaces and facts;
- explanations describe architecture and rationale;
- ADRs preserve durable decisions and consequences;
- generated reports are the only authority for changing counts, fingerprints,
  branch integration state and next-work selection.

For every implementation change, update the smallest existing document that
owns the affected behavior. Do not create a progress diary, branch handoff,
duplicate overview or one-page-per-feature note.

A code-only diff is not evidence that documentation is unaffected. Before
pushing, use the `docs/index.md` task routing table to identify the smallest
existing document that owns any changed:

- responsibility or mutation owner;
- public contract or schema;
- command or contributor workflow;
- supported behavior or limitation;
- compiler, capability, or runtime-component boundary;
- replay, privacy, protocol, or browser behavior;
- extension path.

Update that document when its owned behavior changed. When no documentation
change is required, that conclusion must come from reviewing the owning
document, not from the absence of an edited Markdown file.

Documentation fitness functions validate the documentation and generated
artifacts that exist. They do not by themselves prove that a required
documentation update was not omitted. The generated finalizer owns downstream
status and audit ordering whenever code, documentation, or machine-readable
inputs may have changed.

Living documentation must:

- describe the immediate current state in present tense;
- distinguish implemented behavior from explicit limitations;
- avoid PR/SHA/run/test/card totals and other volatile facts;
- link to generated status instead of copying it;
- have one primary audience and one documentation purpose;
- use sentence-case headings, literal language and repository-relative links;
- identify commands that are safe to copy;
- delete or rewrite superseded guidance in the same PR;
- update `docs/index.md` when files move, appear or disappear.

Use an ADR only for a durable architecture decision whose alternatives and
consequences future contributors need. Supersede accepted ADRs; do not rewrite
their historical decision. Keep historical narrative only in ADRs and
`CHANGELOG.md`.

Run the documentation fitness functions after any Markdown change:

```powershell
.\.venv\Scripts\python.exe scripts\validate_documentation.py --check
.\.venv\Scripts\python.exe scripts\finalize_generated.py --check
```

If a document disagrees with code or generated evidence, fix or remove the
document. Never preserve a stale statement for continuity.
