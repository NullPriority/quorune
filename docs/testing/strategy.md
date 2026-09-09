---
title: "Testing strategy"
status: "current"
authoritative_source: "tests, quick gate, sharded PR certification, and nightly assurance"
verified: "2026-08-12"
audience: "all contributors"
maintenance: "hand-maintained"
---

# Testing strategy

Tests prove bounded behavior; generated inventories do not prove rules
correctness. Each change starts with focused tests and expands validation in
proportion to risk.

## Evidence layers

1. Unit tests cover typed values, parsers, legality predicates, and isolated
   rules helpers.
2. Transaction tests cover legal and illegal commands, rollback, costs,
   choices, state-based actions, and event ordering.
3. Replay tests prove canonical commands reconstruct the same authoritative
   state under pinned fingerprints.
4. Projection/privacy tests prove each principal sees exactly its allowed view.
5. Interaction tests cover capability pairs and high-risk multi-effect cases.
6. Browser tests prove the untrusted UI invokes the same server-issued actions
   across isolated contexts, reconnect, and persistence.
7. Generated CR/Oracle coverage records source linkage, review state, and
   residuals; it is not executable evidence by itself.

Capability evidence is an explicit generated relationship, not an inferred
test-name match. A migrated semantic family supplies positive and negative
behavior, malformed-input rollback, exact replay, and implementation-mutation
evidence. The tap-state family additionally characterizes CR 122.1d stun
replacement, effective creature types, phased-out objects, and no-op event
suppression while retaining honest blockers for the broader systems.

Compiler expectations identify one of three purposes:

- isolated leaf-parser acceptance or rejection;
- whole-card rejection caused by a specifically named unsupported sibling; or
- real-Oracle integration under the current supported semantics.

When integration makes a real card supported, convert its expectation to a
positive witness. Preserve a remaining exclusion with a deliberately
constructed boundary fixture or another independently justified case. Do not
derive expected support from the compiler under test or repeatedly substitute
an unrelated real card without naming the blocker being protected.

For a semantic scope expansion, select the reachable boundaries that can change
the result. Bounded stateful or property exploration should cover meaningful
sequences around control changes, ability removal and restoration, source
departure and reentry, replacement choices, save/load, and rollback where
applicable. Retain a minimized failing sequence as a readable regression. The
[rules assurance model](../rules/assurance-model.md) owns the expected-behavior
contract and semantic distinctions; the
[interaction guide](interaction-coverage.md) owns composition selection.

## Bounded match readiness

Match readiness is a separate incremental milestone, not an inference from card
closure. `tests/test_commander_match_readiness.py` owns the small source-
controlled reference table over the maintained Mishra and Zimone decks and the
existing format, trust-closure, server-session, browser-soak, and replay owners.
It labels every scenario as reviewed-compatible or a rules-runtime fixture; it
does not claim strict capability-only readiness. The bounded scenario set
covers natural completion, supported legal-action availability, private choices
and principal-correct projection, reconnect or save/load during a pending
decision, exact replay, and classification of every unsupported-rule stop.

An expected declared unsupported interaction is a coverage limitation. An
unexpected failure inside a claimed supported scenario is a defect even when
mutation failed closed. The reference set grows independently of the corpus;
neither every card nor every deck must satisfy the milestone before the next
safe rules harvest. The maintained decks are Commander-legal and reviewed-
compatible, while strict readiness remains blocked by the incomplete format
capability inventory plus current missing, legacy/mixed, and unbound per-card
program dependencies described by the
[trust-closure contract](../architecture/trust-closure.md).

During iteration, run the new/focused tests and adjacent impacted modules. The
deterministic `scripts/quick_gate.py` classifier includes both committed and
working-tree changes and selects the relevant modules, functional shards, and
validation commands. Its dry-run output is reviewable before execution.

The ordinary merge authority is the public pull-request workflow for the exact
head SHA. Twelve duration-ordered Ubuntu functional shards run in parallel with generated
and architecture checks, package/clean-install validation, focused or complete
Windows coverage, and an isolated headless browser smoke or full journey set.
The stable `PR / Certification` job fails closed unless every required job
succeeds. Compact `main` smoke catches integration mistakes after merge; the
nightly workflow owns the strictly certified cross-platform shard matrix, full browser journeys,
large deterministic property budgets, mutation/soak checks, current Oracle
censuses, and dependency audits.

Use `scripts/local_merge_gate.py` for releases and exceptional high-risk
persistence, replay, privacy, or packaging work, not as the default inner loop.
See the [CI pipeline guide](../development/ci-pipeline.md) for shard ownership,
two-slot worktrees, and recovery commands. The generated
[platform status](../PLATFORM_IMPLEMENTATION_STATUS.md) remains the source for
current counts.
