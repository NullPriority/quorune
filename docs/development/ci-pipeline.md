---
title: "CI pipeline and two-slot development"
status: "current"
authoritative_source: "GitHub workflows, platform/test-shards.json, and local gate scripts"
verified: "2026-08-21"
audience: "contributors and maintainers"
maintenance: "hand-maintained"
---

# CI pipeline and two-slot development

The repository uses narrow, opt-in local feedback and exact-head public
certification. GitHub Actions is the ordinary broad-test and merge authority.
The workflow never requires a visible browser.

## Two development slots

Keep at most two substantive branches active:

- Slot A is pushed and undergoing pull-request certification.
- Slot B is a separate worktree containing the next independent rules batch.

Create Slot B from current remote `main` while Slot A is running:

```powershell
git fetch origin --prune
git worktree add ..\quorune-next -b <next-branch> origin/main
Set-Location ..\quorune-next
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts\worktree_bootstrap.py --install-hook `
  --db "C:\path\to\the\pinned\scryfall-current.sqlite3"
```

The readiness command is read-only except for the explicit hook-install mode,
which changes only repository-local Git configuration and refuses to overwrite
a foreign hook policy. Database lookup uses `--db`, `MTG_CARD_DB`, then the
worktree-local `data/scryfall-current.sqlite3`. The command compares that
database with the tracked compiler-corpus snapshot, distinguishes missing,
stale, and invalid inputs, validates primary test-shard ownership, and prints
the exact finalizer command for the detected platform. Run it without
`--install-hook` to recheck an existing worktree.

Never rebase or rewrite Slot A while its exact head is being certified. If its
CI fails, preserve coherent Slot B work, fix Slot A in its own worktree, push a
new immutable head, and let stale runs cancel. After Slot A merges, fetch and
rebase Slot B only when its changes actually overlap the merged work.

Clean up a merged slot only after confirming its pull request and `main` SHA:

```powershell
git fetch origin --prune
git worktree remove <merged-worktree-path>
git branch -d <merged-branch>
git push origin --delete <merged-branch>
```

Do not delete a branch with unique work, an active run, or an unmerged pull
request.

## Local impact inspection

Do not run broad local suites as the ordinary workflow. If feedback is
materially useful, run only the exact new test and smallest adjacent impacted
selection. Before push, inspect what CI will select:

```powershell
.\.venv\Scripts\python.exe scripts/quick_gate.py --dry-run
```

`platform/change-impact-policy.json` is the versioned many-to-many path/check
policy consumed by `scripts/change_impact.py`, `scripts/quick_gate.py`, and
`scripts/ci_plan.py`. It maps normalized paths to the manifest in
`platform/test-shards.json`, generated checks, and platform gates. Internal
rules modules are never classified by generic words such as `action` or
`choice`; browser-facing protocol, projection, action-catalog, choice-form,
server, lifecycle, and persistence paths are explicit. `engine.py` and
`session.py` no longer imply every browser journey by path alone: the typed
subsystem changed alongside them selects any focused public behavior. A
compiler-only change with no browser-facing runtime or schema change therefore
keeps the compact smoke only. For responsibilities still inside the legacy
engine, the planner maps changed Python hunks in both the base and candidate
trees to qualified function owners. Changes to the enumerated priority, yield,
and action-opportunity methods require complete browser E2E; unrelated engine
orchestration does not inherit that cost. Cross-cutting protection and
attachment sources deliberately select compiler, replacement, targeting, and
state-action owners so a source-correctness regression cannot escape through a
single narrow shard. When explicitly executed for diagnosis,
`scripts/quick_gate.py` includes
committed and working-tree changes, validates Python 3.12, compiles Python,
builds the compact card database when necessary, runs directly changed tests
and affected functional shards, and selects relevant generated, architecture,
rules, repository, package, or browser-build checks.

The local quick gate does not run Playwright journeys. Browser-sensitive work
gets generated-type, typecheck, and production-build checks locally; isolated
headless Chromium belongs to CI. Never add a command that opens, focuses, or
navigates the user's browser.

## Generated artifact finalization

The finalizer is development-time certification, not an application runtime
dependency. Neither the server nor browser invokes it. Its purpose is to keep
tracked coverage, architecture, protocol, and status artifacts synchronized
with their authoritative source before Git publishes a commit.

`platform/generated-artifacts.json` is the canonical ownership and dependency
manifest for every tracked generated artifact. Its versioned discovery policy
finds artifacts through generated path prefixes, top-level pinned-rules JSON,
generated-document metadata, explicit binary/report paths, and embedded
third-party generator markers. The completeness validator rejects unowned
discovered artifacts, duplicate owners, repository escapes, missing registered
outputs, registered outputs that are not Git-tracked or independently
discoverable, and dependency cycles before any writer runs.

The manifest does not replace specialized source authorities. Pinned rules
snapshots, browser protocol bindings, durable baseline history, and the public
protocol demo remain deliberate manual or separately generated assets, while
their paths and checks still have one manifest owner. Deterministic Python
reports declare their writer and checker and whether writing is automatic,
database-backed, or a deliberate manual baseline operation. Reusable owners
also declare direct input groups, implementation entry points, database
identity, execution class, and reuse policy; adding a new source read requires
updating that closure. CI and the local
impact plan invoke the same interface. Adding a file below `coverage/` or
`demo/`, a top-level `rules/*.json` file, a generated-status Markdown document,
or a file with a registered generator marker requires adding that output to its
owner in the same change.

Owner reuse conservatively follows the complete Python import closure. The
compiler-identity sentinel uses the compiler generator's implementation closure
but treats package initializers as leaf boundaries: the initializer itself is
semantic input, while modules re-exported only through that initializer do not
require a compiler or schema bump. A module imported directly by the compiler
closure remains semantic input and fails closed when its identity is unchanged.

The change-impact policy may also link a production owner directly to named
neighboring test modules. Compiler promotion families use these links so a
change to a static-characteristic, activated-cost, prevention, or central
program-registration owner makes its positive and residual expectations
visible in the focused plan. The links supplement the authoritative cloud
shard; they do not turn a full shard into a required local run.

Run write mode after the coherent source/test/documentation worktree is complete
and before the final commit; inspect and stage its outputs with the source
change:

```powershell
.\.venv\Scripts\python.exe scripts\finalize_generated.py --write
```

Compiler, capability, CardProgram, and card-support changes require the pinned
database census in the same finalization run:

```powershell
.\.venv\Scripts\python.exe scripts\finalize_generated.py --write --db data\scryfall-current.sqlite3
```

### Cloud-generated artifact bundles

`.github/workflows/generated-artifacts.yml` offloads the same governed writers
to GitHub-hosted runners. Source-changing pull-request events and every `main`
push first derive an affected-owner plan from schema-3 input declarations. A
pre-corpus quick-gate phase records the affected tests for the ordinary PR
matrix, but executes only runtime/compile validation, the generated manifest
plan, and the compiler-identity sentinel before any census. The workflow is not
subscribed to `ready_for_review`, so moving an unchanged draft into review does
not restart cloud generation.

Each reusable owner is keyed by its Git-clean implementation and direct-source
closure, dependency-output fingerprints, canonical manifest row, and governed
pinned-database identity where applicable. The key deliberately excludes the
commit SHA. A PR owner artifact can therefore be reused by a content-identical
merge commit, while its staged envelope and the complete
`cloud-generated-<commit>` bundle remain exact-commit and exact-source bound.
Cross-run lookup accepts artifacts only from a completed execution of this
workflow whose head repository is Quorune itself. A successfully checked owner
receipt remains reusable when a later downstream job fails or the workflow is
cancelled; a failed/cancelled owner that published no receipt is a cache miss
and retries. Fork artifacts cannot seed `main` reuse.
The pinned database is also cached from its snapshot and builder inputs, then
validated by SQLite integrity, schema, and row cardinalities before use. The
workflow has read-only repository permissions and never commits or opens a pull
request. On `main`, assembled output bytes must match the merge.

Each owner checks only its own completed output. Workflow `needs` edges and
downloaded owner artifacts supply upstream state; the final bundle check is the
single fail-closed validation of the complete dependency graph. This keeps a
manual upstream verifier from rejecting intentionally stale downstream files
before their owning cloud jobs can regenerate them.

Generated-owner jobs that consume only the exact source tree use depth-1
checkouts. Source planning and rules-scheduler harvest provenance retain full
commit topology through blobless partial checkouts, fetching historical blobs
only when their comparisons require them. The final bundle deliberately keeps
one full-history checkout because repository policy audits every reachable Git
object. This prevents the parallel owner fan-out from independently downloading
the repository's complete historical blob set while preserving every job's
actual history contract.

Reusable-piece baseline and delta reports take architecture debt dimensions
from the reviewed guard baseline. The full architecture audit follows reusable
generation so its interaction-assurance and subsystem inventories see the final
reusable artifacts; neither owner reads the other's output upstream.

After the parallel and dependency-ordered owners are assembled, the bundle job
requires one strict reusable receipt for every automatic owner, runs every
noncacheable/manual check and cross-cutting policy check in `--assemble` mode,
and writes the local exact-head finalization receipt. The validated reusable
receipts replace duplicate automatic-owner checks in this tail; assembly never
reruns an owner. A main run then requires the assembled bytes to match the
commit.

Ordinary feature branches run this workflow automatically when opened,
reopened, or synchronized:

```powershell
$env:QUORUNE_CLOUD_SOURCE_CHECKPOINT_REASON = "seed exact-source cloud generation"
git push -u origin <branch>
Remove-Item Env:QUORUNE_CLOUD_SOURCE_CHECKPOINT_REASON
gh run list --workflow generated-artifacts.yml --limit 10
gh run download <run-id> --name cloud-generated-<source-sha> `
  --dir local\cloud-generated-download
.\.venv\Scripts\python.exe scripts\cloud_generated_artifacts.py install-bundle `
  --bundle-dir local\cloud-generated-download --expected-commit <source-sha>
```

The named checkpoint mode is restricted to non-`main` branches. It still runs
test-shard/dependency validation and the compiler-identity sentinel, but allows
the intermediate push before the expensive generated bundle and final receipt
exist. Never use it for the subsequent generated-output push or as merge
evidence.

For recovery or diagnosis only, a manual exact-ref dispatch remains available:

```powershell
gh workflow run generated-artifacts.yml --ref main -f ref=<source-sha>
```

The installer accepts only the current `HEAD`, validates the bundle receipt,
the canonical manifest output set, the source-tree fingerprint, and each file
hash, then writes a local ordinary finalization receipt for the pre-push hook.
Before copying an output, it compares the downloaded and working-tree files
through Git's path-specific clean filters. Equivalent LF/CRLF representations
therefore preserve the local checkout bytes and do not dirty a Windows index.
Inspect and stage the generated changes with the authoritative source before
the final commit. Exact-head PR CI remains mandatory. A cloud bundle is not
permission to merge stale sources, defer generated changes to a later PR, or
replace manual pinned-rules, protocol-binding, demo, or performance-baseline
workflows.

Write mode runs generators in topological order and repeats only changed
generators and their downstream automatic or derived-only consumers until a
bounded pass changes nothing. A requested database-backed corpus rebuild occurs
only on the first pass because the DAG already orders all of its consumers
afterward. It then runs all freshness checks, documentation validation, and
diff hygiene. Pass `--db <path>` or set
`MTG_CARD_DB` when a card-data-backed frontier or full reusable-piece rebuild is
required. The manifest owns the full/Commander Oracle and CardProgram census
before the card-unlock frontier, so the frontier cannot compare current source
against stale status counts. The reusable-piece writer can refresh
architecture-derived delta metadata without rebuilding the pinned corpus.
The automatic `rules-derived` owner rebuilds conformance cases, pinned manifest
hashes, the mechanic registry, and rules/mechanics coverage from authoritative
review overlays and mechanic contracts without downloading or reparsing the
Comprehensive Rules. It normalizes temporary text outputs to LF before
freshness comparison and hashing, so a signed Linux bundle remains current on a
Windows checkout instead of producing false CRLF-only staleness. The rules
scheduler and platform status explicitly depend on that owner, so a rules review
or contract edit cannot leave their inputs stale while the finalizer still
reports success.
The database-backed `work-selection-cohort-measurements` owner joins static
candidate probe definitions to the current card-unlock frontier and pinned
Oracle lines. It writes current cohort fingerprints and observed card, ability,
residual, and blocker-closure counts to
`coverage/work-selection-cohort-measurements.json`; static selection policy
contains no copied frontier fingerprint or observed count. Family-level bundle
closure remains `upper_bound_only` until that generated probe establishes one
executable grammar and lower bound. When implementation and measurement share
one feature checkpoint, the owner also seals the source-checkpoint frontier's
selected measurement as a content-fingerprinted transition receipt before the
new corpus frontier retires that completed cohort. Later writes preserve only
the still-declared transition receipt; policy stores its stable measurement ID,
not its observed counts or frontier fingerprint.
The rules-scheduler owner also maintains
`coverage/harvest-outcome-history.json`. Existing historical rows retain their
immutable Git provenance, while new semantic transitions use base and head
content-receipt fingerprints over the Commander CardProgram corpus, Oracle
coverage, card-unlock frontier, interaction inventory, and architecture audit.
The transition declaration carries the bundle, candidate, family, capability,
generated-measurement, and compiler identities before corpus generation. Once
the generated head receipts exist, the same feature fixed point appends the
measured lower bound and actual outcome automatically. Base and head semantic
receipts plus the transition-measurement receipt are content identities with no
feature-commit field. They survive squash, so review, merge, and the next
selector pass require neither a merge-commit association nor a bookkeeping
follow-up.
If no implementation-eligible cohort exists, the same selector may choose one
`cohort_measurement` task. That task pins the corpus filter, owner hypothesis,
grammar boundary, exclusions, cards/residuals to inspect, probe effort, and
upgrade evidence; it grants no gameplay trust or card support.
Implementation-eligible work always outranks a measurement in the same
correctness class. Bundle selection reports shared owner, source-context,
grammar, card, ability, residual, blocker-closure, and cycle-hour fields before
ranking within the machine-readable correctness-first class order.
A `bounded_executable` declaration is rechecked against every current member
occurrence, lowerable ability, card row, and material residual. Census drift
returns it to `requires_bounded_cohort` rather than preserving a stale
selection.
Performance baselines remain
manual because observed latency is review evidence, not an automatic rewrite.
Use `--check` for read-only diagnosis and in CI; a successful `--write` already
performs that verification, so do not run both commands consecutively.

If a full database-backed write fails at a later registered owner, correct the
source problem and use `--resume-from <generator-id>` only when the correction
cannot affect earlier owners. The canonical coordinator reruns that owner and
all descendants, then performs every normal freshness and policy check. This
avoids repeating the expensive corpus while preserving dependency and final
verification guarantees; do not invoke individual writers by hand. The normal
first run and pre-push hook never use resume mode.

The final verification phase includes the architecture policy validator, not
only generated-file freshness. This closes the failure mode where every report
was current but a new semantic operation, direct write, or oversized boundary
had not been added to the reviewed architecture baseline. Run write mode before
the final commit. A successful write stores a worktree-local receipt in Git
metadata. The pre-push hook verifies that receipt and blocks publication on
either generated drift or architecture-policy failure without repeating the
full corpus when the finalized inputs and outputs are identical.

The worktree readiness command installs and verifies the tracked pre-push hook.
The hook-only installer remains available when repairing an existing setup:

```powershell
.\.venv\Scripts\python.exe scripts\install_dev_hooks.py
```

This is deliberately a pre-push hook, not a pre-commit generator. Derived
changes must be reviewed and committed with their authoritative source, while
database-backed corpus generation is too expensive for every checkpoint
commit. Maintainers and coding agents run the finalizer before the final commit;
the hook accepts an exact receipt or falls back to `--write --fail-on-change`
and rejects publication if any writer still changes the tree. The receipt is
bound to tracked source blobs, every registered output, manifest completeness,
and the selected database file identity. A commit containing the already
finalized bytes preserves the receipt; any later relevant edit invalidates it.
New files intended for that commit must already be Git-tracked or staged when
the finalizer runs so they participate in the source fingerprint.
A configured worktree reports `.githooks` from `git config --get
core.hooksPath`.

The installer sets the local `core.hooksPath` to `.githooks` and refuses to
overwrite another hook policy. The hook is a backstop that uses the
worktree-local Python. It first runs `scripts/test_shards.py validate`, because
an unassigned discovered test module makes PR planning fail before any matrix
job can run. It next runs
`scripts/build_test_database.py validate-ci-dependencies`, so a compact-database
card, Oracle ID, deck, fixture, or shard-closure omission fails before the push.
It accepts the exact existing receipt before inferring another database. A
database-bound receipt verifies the database path and fingerprint recorded by
the successful finalization, so a secondary worktree can reuse the canonical
worktree's pinned database without repeating the corpus. An ordinary receipt
still proves every database-backed freshness check; compiler or corpus drift
therefore makes either receipt stale. If no receipt matches, the hook uses the
worktree database when present. An explicit `MTG_CARD_DB` selection always
requires a receipt bound to that exact database. The fallback runs generated
write mode and rejects the push when outputs need a commit. It never amends a
commit.
Pull-request CI remains check-only and authoritative.

Keep generated-governance tests tied to identities from the canonical manifest
or registry rather than separately maintained totals or copied identifier sets.
Those literals turn every legitimate promotion into an unrelated CI repair.
Compiler-only tests should construct their input `CardRecord` directly; only
runtime integration tests should require the compact CI card database. When a
database-backed fixture is genuinely required, identify every workflow and
local-gate database builder that consumes it before publishing the branch.

`tests/fixtures/compact-ci-fixtures.json` is the single machine-readable input
set for the shared compact CI database. Every Linux, Windows, generated,
browser, main-smoke, nightly, quick-gate, and local-gate build invokes:

```text
python scripts/build_test_database.py build-ci --output <job-specific-path>
```

`python scripts/build_test_database.py validate-ci` fails when the manifest is
malformed, contains duplicate, missing, escaping, or noncanonical paths, or a
registered consumer reintroduces its own `--fixture` list. Add a required
fixture once to the manifest; all consumers retain isolated output paths while
receiving the same composed card set automatically.

`python scripts/build_test_database.py validate-ci-dependencies` proves that
the set is sufficient. The structural analyzer discovers exact `CardDatabase`
name and Oracle-ID lookups, helper wrappers (including keyword/default values),
and `DeckLoader` paths from every module assigned by
`platform/test-shards.json`. It then builds a fresh database from the canonical
manifest, loads every discovered deck, resolves every dependency, and verifies
that no `full_database_only` module is assigned to a compact shard. It also
indexes fixture ownership and rejects conflicting canonical names, aliases, or
Oracle IDs.

Genuinely dynamic requirements must be declared in the existing manifest by
exact source path and symbol, with sorted card, Oracle-ID, deck, or fixture
requirements and a nonempty rationale. Undeclared dynamics and stale
declarations both fail closed. Do not add another fixture list or compact
database builder. The generated
`coverage/compact-ci-card-dependencies.json` and `.md` companions record the
current identities, owners, source provenance, and per-shard closure; they do
not duplicate card payloads.

Pull-request planning runs this validation before functional matrices are
released, and Windows, main-smoke, nightly, quick-gate, local-gate, and
pre-push paths enforce the same closure. A missing fixture therefore fails at
the dependency boundary instead of surfacing much later as unrelated runtime
test errors.

The full `scripts/local_merge_gate.py` is not a default development step. Run a
broad local gate only when the user explicitly asks or while diagnosing a
CI-only/release-critical persistence, replay, privacy, or packaging failure.
Otherwise push the coherent exact head and use the CI window for independent
Slot B work.

## Pull-request certification

`.github/workflows/ci.yml` runs these independent jobs:

- twelve duration-ordered Ubuntu functional shards;
- canonical generated-artifact finalization checks from the ownership
  manifest, followed by rules, documentation, repository, and architecture
  validation;
- wheel build and clean-install verification;
- a focused Windows compatibility overlay for ordinary changes;
- for platform-sensitive changes or the `windows-full` label, all thirteen
  authoritative primary shards on isolated Windows runners and Python
  processes, with `fail-fast: false`, at most five concurrent jobs,
  per-shard compact databases and runtime roots, and no shared writable state;
- one separate Windows wheel build and clean-install verification, followed by
  `PR / Windows Certification`, which fails closed on the wrong mode, missing,
  skipped, failed, duplicate, zero-test, wrong-platform, wrong-backend, stale
  collection, or incomplete module-timing results, a manifest partition gap,
  or package failure;
- browser build plus a compact authoritative four-context lifecycle smoke;
- focused `mana-action`, `combat`, or `turn-draw` Playwright journeys selected
  by the affected typed rules owner (or the matching `browser-*` label);
- three deterministic complete Playwright groups for browser, protocol, projection,
  reconnect, room, WebSocket, lifecycle, persistence, browser-facing choice or
  action schema changes, workflow changes, natural-winner critical rules, or
  the `browser-full` label. The nonempty `lifecycle`, `rules`, and `soak`
  groups use distinct ports, runtime directories, and SQLite databases.

The compact smoke is the bounded reconnect/lifecycle journey: it starts the
real server, creates four seat-isolated tabs, validates private hands, submits
accepted mulligan commands including an exact retry, survives pause/resume and
reconnect, and closes every context. It does not play a natural game to a
winner. Natural completion remains in the `soak` group and runs when browser,
persistence, replay, Commander-damage, combat-completion, state-based-loss, or
workflow ownership changes. Focused journey tags are closed policy values in
`platform/change-impact-policy.json`; adding an arbitrary test title cannot
silently expand or bypass the gate.

The final `PR / Certification` job receives the stable Windows certification
result and every other required job through `needs`, and fails unless all
succeeded. Protect `main` with the exact required status context
`PR / Certification`. After verifying those dependencies, the job publishes an
untracked `exact-head-certification-<run-id>` artifact. Its strict receipt pins
the repository, pull request, exact PR-head SHA, publication workflow run,
original evidence workflow run, executed-or-reused mode, complete required
check suite, fingerprint algorithm, and tracked source-tree fingerprint. It
does not contain or predict the eventual merge SHA. An unchanged-head metadata
event runs only `PR / Plan`; it neither publishes a certification receipt nor
launches, cancels, or waits for a regression matrix. The successful
source-changing run for that exact head remains the sole certification owner.

The pre-sharding public baseline is run `31025126367`: its single Windows
discovery process executed the complete test allocation in 2,265.245 seconds
(37 minutes, 45.245 seconds) before reporting the already-corrected
generated-audit drift.
Use the exact-head matrix metrics—not that historical total—to decide whether
the five-runner ceiling or shard allocation should change.

Do not use `gh pr merge --auto` until branch protection is confirmed. Without a
required check, GitHub may merge immediately while jobs are still running.
Once protection is active, auto-merge is safe only for the immutable SHA whose
certification is in progress.

The nonblocking metrics job records observed queue, job, step, and critical-path
durations plus Playwright journey duration, status, retries, failure class,
browser-context count, accepted command count, authoritative/projected
revisions, and measured persistence/review time. It also reports each Windows
shard's queue, setup, test and total duration, executed test count, the one-time
package duration, the Windows critical path, and actual overlapping test-runner
concurrency. Functional Linux reports add the exact backend, worker count,
collection fingerprint, wall duration, and cumulative worker time per module.
Raw JSON reports and the combined `ci-metrics` artifact are retained for 14
days so future shard changes use measured history. Cache-hit rate, agent idle
time, and stale-run cancellation remain `null` when GitHub does not expose
measured data; the reporting code never estimates them as observations.

Long browser journeys use one shared progress driver rather than nested timeout
loops. It observes the decision ID, phase/step, active and priority players,
view/state revisions, accepted command and event counts, latest event, actor
queue, and pending persistence. Ninety seconds without a real change fails with
a compact snapshot and exact one-test rerun command. Ordinary command
acknowledgements still wait for authoritative durability, while review artifacts
remain derived and are generated only for paused or terminal records.

`platform/readiness-source.json` contains durable product and certification
policy only. Pull-request numbers, exact heads, workflow runs, merge SHAs,
runtime branches, and transient integration chronology belong to GitHub and the
untracked certification receipt. The generated readiness report fingerprints
its actual source, package, stable test-shard inventory, rules, and CardProgram
inputs. Exact tracked-source equivalence belongs only to the certification
receipt and main-smoke verification. Environment-sensitive executed-test totals
remain CI metrics rather than tracked readiness state.

Deterministic failures that escape the quick gate are recorded in
`platform/ci-escape-source.json`. The generated
`coverage/ci-escape-report.json` and `.md` classify each failure, its direct
regression, and the impact-edge disposition. Push counts and Slot B idle time
remain null when they cannot be observed; workflow-run counts are not relabeled
as pushes.

## Pull-request description gate

`PR / Plan` runs `scripts/validate_pr_body.py` before change-impact planning or
any expensive matrix job. It reads the pull-request event payload without a
GitHub API call and fails deterministically when the tracked template is still
untouched, a required section or evidence result is blank, an N/A has no
reason, a safety assertion remains unchecked, or the generated base/head block
does not match the event's immutable base and exact head. Generate the copyable
block after the final source commit with:

```powershell
.\.venv\Scripts\python.exe scripts\pr_evidence.py `
  --base <base-sha> --head <head-sha> --format markdown
```

The command reads represented family and capability IDs from the head's
semantic-transition declaration and reconciles Oracle ability promotions,
aggregate CardProgram record changes, structural carriers, material residuals,
interactions, actual PR-source architecture deltas, the separately reviewed
architecture baseline, and production/test/generated line changes. Missing
source metadata remains an explicit reasoned N/A rather than an inferred
identity. Editing the description restarts only the gate, so a contributor can
correct metadata without changing the certified source tree or launching
Linux, Windows, package, generated, or browser jobs. An `edited` event never
falls back to the complete matrix and never publishes a substitute
certification receipt. Open, synchronize, and reopen events remain the only
complete-matrix pull-request events.

For a later source commit, generate the exact base/head block after committing
locally, update the pull-request description while the new commit is still
unpublished, and then push. The metadata-only event may briefly reject that
future head against the still-published old one; the following synchronize
event carries the matching body snapshot and runs the one authoritative matrix.
Pushing first and editing afterward is incorrect because the synchronize event
retains its immutable pre-edit description and fails before the matrix.

The PR workflow intentionally does not subscribe to `ready_for_review`; moving
an unchanged draft into review therefore does not start regression by itself.
Finalize the full template before the first source push and do not replace a
green run's pending-CI sentence with a passed-CI sentence: that description
edit is unnecessary even though it is Plan-only.

Generated work named in the description must cite the canonical
`scripts/finalize_generated.py --write` command. A claimed broad local pass
must include its exact command and numeric result. A claimed broad CI pass must
link the authoritative GitHub Actions run; before that run exists, state that
required exact-head CI is pending rather than predicting its outcome.

The same gate compares the candidate versions of
`platform/readiness-source.json` and
`platform/architecture-audit-source.json` with the pull request's base. Newly
written PR numbers, branch coordinates, workflow runs, exact heads, or merge
SHAs fail closed. Unchanged historical content is not reinterpreted, and
`platform/ci-escape-source.json` remains the intentional durable ledger for
observed CI incidents.

## Main and nightly assurance

`.github/workflows/main-smoke.yml` runs after each push to `main`. It checks a
compact replay/server suite, generated integration state, pinned rules, wheel
metadata, and the production browser build. It is an integration alarm, not a
second complete pre-merge suite. Before those checks, it resolves the pull
request associated with the current merge commit from both GitHub's
commit-association endpoint and the recent closed-pull-request listing. The
latter is required because GitHub may temporarily or indefinitely return no
commit association for a squash merge. Duplicate payloads for the same PR are
deduplicated by number, while zero or multiple matching PRs still fail closed.
The workflow then finds a successful PR run for that exact head, downloads its
live certification receipt, and requires the current tracked source tree to
have the same fingerprint. A squash merge passes without a follow-up status
commit because commit identity is deliberately not the equivalence boundary;
a materially different tree, missing/stale receipt, failed gate, direct push,
or mismatched GitHub coordinate fails closed.

`.github/workflows/nightly.yml` owns expensive breadth:

- every deterministic primary Python shard on Ubuntu and Windows, launched in
  one slow-first matrix with six concurrent jobs and strict result
  certification;
- all three isolated headless Chromium groups, including the natural-winner
  soak;
- at least 100,000 deterministic property transitions across parallel jobs;
- focused implementation mutations, natural-winner/persistence soak, and
  performance/repository checks;
- current Scryfall ingestion and full/Commander Oracle and CardProgram
  censuses as artifacts;
- Python and npm dependency audits.

Nightly failures are real regressions or assurance debt. Fix them on a focused
branch; do not weaken the nightly budget to make a failure disappear.

## Headless browser commands

The public workflow is authoritative, but a focused local reproduction may be
run headlessly after assigning isolated paths and ports. None of these commands
opens a visible browser or HTML report:

```powershell
$env:MTG_CARD_DB = "data/test-ci-smoke.sqlite3"
$env:MTG_E2E_SERVER_PORT = "18081"
$env:MTG_E2E_WEB_PORT = "15171"
$env:MTG_E2E_RUNTIME_DIR = "../local/playwright-smoke"
$env:MTG_PLAYWRIGHT_JSON = "../local/playwright-smoke.json"
npm run e2e:smoke --prefix web

Set-Location web
npx playwright test --grep "@browser-lifecycle"
npx playwright test --grep "@browser-rules"
npx playwright test --grep "@browser-soak"
```

Use different database, runtime, and port values when groups run concurrently.
On failure, prefer the exact `--grep` command printed by the progress diagnostic.

## Shard maintenance

Every `tests/test_*.py` module belongs to exactly one primary shard in
`platform/test-shards.json`. Overlay suites such as `main-smoke`,
`windows-compat`, and `nightly-property` may intentionally reuse modules. The
named compiler, targeting, casting, combat, and other semantic suites are also
overlays: they preserve focused change-impact routing while neutral
`functional-NN` primary shards distribute complete modules by measured cloud
duration.

The PR workflow has a checked public concurrency budget of 20 jobs and reserves
at least two slots for recovery. `scripts/ci_plan.py` derives the functional
matrix and both OS limits from the actual browser and Windows impact modes:

| Browser mode | Windows mode | Ubuntu functional | Windows | Browser | Peak | Reserve |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| smoke | compatibility | 12 | 1 | 1 | 17 | 3 |
| full | compatibility | 11 | 1 | 3 | 18 | 2 |
| smoke | full | 6 | 8 | 1 | 18 | 2 |
| full | full | 5 | 7 | 3 | 18 | 2 |

Three fixed jobs cover generated validation and the two package builds.
Changing a matrix without reconciling all four budgets fails the focused CI
policy tests. When the full Windows matrix is selected, the remaining shard
slots are divided by a checked 4:5 Ubuntu-to-Windows duration weight. This keeps
the slower Windows tail from dominating while preserving two recovery slots.
The declared matrix order is also significant: GitHub creates
matrix jobs in declaration order, so `platform/test-shards.json` owns a
slow-first order derived from exact-head duration artifacts.

Each functional Ubuntu or Windows job uses four fixed pytest-xdist workers with
`--dist loadfile`. A test module stays in one worker process, which preserves
module fixtures and avoids splitting a unittest class across processes. Before
execution the canonical unittest loader records every sorted test ID. Every
xdist worker must collect that exact set, and xdist also requires identical
worker collections. Any missing, additional, duplicate, or non-unittest item
fails the shard. Worker count is deliberately fixed rather than inferred from
the host, so resource use and timing comparisons remain reproducible.

Generated validation remains on the sequential unittest backend because it
exercises repository-wide generated and governance state. The sequential
runner is also the local default and compatibility fallback; pytest-xdist is
an explicit functional-CI backend, not a replacement test inventory. Result
schema v2 records platform, backend, fixed worker policy, collection parity,
the canonical collection fingerprint, and exact per-module timing coverage.
The Windows and nightly certification jobs independently reconstruct the
manifest collection and reject incomplete or dishonest result sets.

Before the final commit and push, validate ownership after adding, renaming, or
deleting a test module:

```powershell
.\.venv\Scripts\python.exe scripts/test_shards.py validate
```

Every functional primary shard is directly reproducible on Windows and can
write the same compact result record consumed by public certification and
metrics:

```powershell
.\.venv\Scripts\python.exe scripts/test_shards.py run functional-01 `
  --backend pytest-xdist --workers 4 --platform windows `
  --result-json local/windows-results/functional-01.json
```

A focused Linux-equivalent parallel reproduction is:

```powershell
.\.venv\Scripts\python.exe scripts/test_shards.py run functional-01 `
  --backend pytest-xdist --workers 4 --platform ubuntu `
  --result-json local/python-results/functional-01.json
```

Use that command only for a directly relevant diagnostic. Public exact-head CI
is the broad authority. Compare its wall duration and per-module cumulative
worker timings with the preserved sequential baseline before changing worker
count or shard allocation.

`generated-validation` is a primary shard, not a second full-discovery pass,
and uses the sequential backend on both operating systems. The complete
Windows and nightly OS partitions therefore execute every discovered test
module exactly once per platform. `windows-compat` remains an intentionally
overlapping focused suite and never runs alongside the full Windows matrix.

Keep functional shard weights close enough to use parallel capacity. Semantic
overlays remain coherent by subsystem; primary execution shards may mix those
overlays but always move complete test modules, never individual test methods.
The generated inventory shard is separate because thousands of small generated
cases have a different runtime profile from behavioral tests.

After a successful exact-head PR run, download its Ubuntu shard results and
rebuild the primary partition from their complete module timing inventory:

```powershell
gh run download <run-id> --pattern 'python-results-*' `
  --dir local/ci-rebalance
.\.venv\Scripts\python.exe scripts/test_shards.py rebalance `
  --results-root local/ci-rebalance --write
```

The command accepts only successful four-worker Ubuntu `loadfile` artifacts,
rejects duplicate, missing, or unknown modules, preserves semantic overlays,
and writes the slowest predicted primary shards first. A newly added module
that is absent from the prior cloud run requires a measured focused estimate,
for example `--estimate test_new_family=42.5`; once CI observes it, later
rebalances use the artifact value. Validate the resulting exact partition and
review the predicted makespans before committing it.

Nightly uses the same manifest order, interleaved Ubuntu then Windows per
shard, with `max-parallel: 6`. Its other browser, property, mutation, corpus,
and security jobs can consume nine runners, so the measured peak is 15 and five
of the public repository's 20 concurrent-job slots remain available. A final
nightly certification requires all seven upstream job families and every one
of the 26 OS/shard result documents.

The fixed four-worker policy matches the four-vCPU standard public-repository
runner and avoids nested oversubscription. The scheduling and runner contracts
are documented by [pytest-xdist distribution modes](https://pytest-xdist.readthedocs.io/en/stable/distribution.html),
[GitHub matrix ordering and `max-parallel`](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax),
[GitHub-hosted runner specifications](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
and [GitHub Actions limits](https://docs.github.com/en/actions/reference/limits).
Do not change worker count or shard order from intuition: compare exact-head
wall time, queue time, and module timings first.

## Recovery and inspection

Inspect current repository activity without opening a browser:

```powershell
gh pr list --state open --limit 50
gh run list --limit 20
gh run view <run-id> --json status,conclusion,headSha,url
gh run view <run-id> --json jobs --jq '.jobs[] | {name,status,conclusion}'
```

If the stable certification context is missing, first inspect the workflow job
graph and `scripts/verify_ci_needs.py`. If the quick gate selects an unexpected
surface, add a deterministic classifier regression before changing the mapping.
Never bypass a failing required check or represent unavailable CI metrics as
observed values.
