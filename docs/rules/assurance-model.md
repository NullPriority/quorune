---
title: "Rules assurance model"
status: "current"
authoritative_source: "pinned rules corpus, conformance records, capability registry, CardPrograms, and generated coverage"
verified: "2026-08-07"
audience: "rules, compiler, engine, and assurance contributors"
maintenance: "hand-maintained"
concern: "rules-assurance"
---

# Rules assurance model

The rules program links a pinned Comprehensive Rules paragraph and Oracle/ruling
snapshot to reusable implemented behavior and executable evidence. It controls
claims; it is not a checklist whose completion can be inferred from a parser
match, witness card, test name, or generated row.

Quorune's current rules support is a partial, snapshot-scoped implementation. Current states,
counts, fingerprints, and blockers are generated in
[rules status](../RULES_COMPLETENESS_STATUS.md),
[compiler status](../COMPILER_COVERAGE_STATUS.md),
[rules coverage](../../coverage/rules-coverage.md),
[conformance coverage](../../coverage/rules-conformance.md), and the
[dependency queue](../RULES_DEPENDENCY_QUEUE.md).

## Source precedence

Use this order when evidence disagrees:

1. pinned Comprehensive Rules, Oracle text, and relevant ruling authority for
   the expected semantic result;
2. implemented executable behavior for what Quorune currently does;
3. versioned schemas, machine-readable policy, and source manifests;
4. independently established behavioral tests and mutation results tied to
   exact components;
5. generated coverage and dependency reports;
6. current explanatory documentation;
7. ADRs and the changelog as historical rationale.

The official downloaded rules prose and full card-data exports remain ignored
local inputs. Tracked indexes retain source IDs, hashes, spans, classification,
dependencies, and reviewed summaries rather than redistributing the prose.

## Assurance artifacts

- `rules/manifest.json` pins derived rules and card-data source identity.
- `rules/rule-index.json`, `glossary-index.json`, and `mechanic-index.json`
  inventory source-linked records without rules prose.
- `rules/conformance-cases.json` stores one stable claim record per reviewed
  rules unit.
- `rules/conformance-reviews/` stores subsystem reviews and evidence mappings.
- `rules/dependency-graph.json` and `platform/rules-subsystems.json` describe
  dependency, ownership, and cross-program scheduling policy. The generated
  rules queue combines that policy with compact-CI, replay/privacy,
  architecture, interaction, and card-frontier evidence without creating a
  competing rules registry.
- `quorune/rules/capability-registry.json` and generated evidence
  define fine-grained executable trust.
- CardPrograms bind Oracle spans to required capabilities and runtime
  descriptors.
- `coverage/*.json` and compressed JSON own complete measured inventories;
  generated Markdown is a navigation and top-state view.

Derived records never replace source meaning. A successful structural validator
proves internal consistency, not rules completeness.

## Expected-behavior contract

Before production edits, each substantive rules family records a small
expected-behavior contract containing:

- the pinned rule, Oracle, and relevant ruling authority;
- admitted grammar and explicit exclusions;
- a minimal state and its expected legal options;
- the expected committed and resolved result; and
- a nearby counterexample that separates the intended result from the most
  plausible incorrect implementation.

The expected answer cannot be copied from current implementation, compiler
output, support classification, generated inventory, or a replay produced by
the code under test. A fresh rules-focused pass or separately authored
expectation table provides practical independence; another engine or reviewer
is not mandatory. Structural inventory tests may compare with an authoritative
inventory, but that comparison is not independent semantic evidence.

Choose the distinctions reachable through the changed owner rather than an
exhaustive Cartesian matrix. Relevant boundaries include owner, controller,
actor, and affected player; one opponent versus opponents combined; unknown
versus known-empty collections; one semantic effect versus independent
instances; stable card identity versus current object incarnation; printed
versus currently applicable abilities; event-time, last-known, and resolution-
time facts; live versus resolution-locked membership; zero in one cost dimension
versus absence of other obligations; and zone-cast authorization versus
alternative-cost applicability. Preserve a relevant distinction in the typed
descriptor and canonical owner when runtime behavior depends on it.

## Classification model

A conformance record distinguishes:

- `unreviewed`: inventoried but not behaviorally assessed;
- `definition_only`: terminology or structure with no independent transition;
- `blocked`: required behavior or dependency is missing;
- `partial`: a bounded represented slice has evidence while the broader claim
  remains open; and
- `passing`: every declared in-scope behavior and dependency has current
  executable evidence for the supported profiles.

Parser statuses such as exact, partial, and unresolved describe Oracle
representation, not rule conformance. Capability-closed and trusted CardProgram
are stronger but separate card/runtime claims. A broad rule remains blocked
when an applicable interaction or dependency is unresolved even if one
subsection or card works.

## Generic card-support path

```text
pinned rules + Oracle + rulings
             |
       typed Oracle IR
             |
    source-spanned CardPrograms
             |
capability + interaction + profile closure
             |
typed queries, proposals, coordinators, and mutation owners
```

Equivalent supported wording benefits every card. Unsupported wording remains
a precise residual and fails strict preflight. Reviewed card overrides are
compatibility exceptions; repeated descriptors must become a generic compiler
production and rules family.

## Trust requirements

Every behavioral claim declares the applicable evidence classes:

- sound offered and accepted actions, plus availability, selection, and
  execution of supported legal options;
- successful supported composition, demonstrated fail-closed rejection of an
  explicitly unsupported composition, and any still-unknown composition as
  distinct outcomes;
- positive behavior and negative/unavailable behavior;
- malformed-input rejection and transactional rollback;
- multiplayer, APNAP, affected-player/controller, and simultaneous choices;
- exact replay, canonical hashes, and save/load for persistent state;
- privacy/projection for hidden information and private choices;
- replacement, prevention, copy, layers, zone identity, and Commander
  interactions where reachable;
- dependency and unsupported-variant fail-closed behavior;
- bounded property or fuzz exploration for meaningful state spaces; and
- a focused killed implementation mutation.

An omitted applicable class requires a reviewed not-applicable rationale.
Evidence IDs must resolve to current tests and implementation components.
Missing dependencies, surviving critical mutations, source drift, or stale
generated data block trust.

Fail-closed rejection establishes a declared support boundary. It cannot prove
that an omitted legal action works inside a scope otherwise claimed as
supported. Correct the supported contract or narrow the claim rather than
broadening unsupported behavior to satisfy an availability test.

## Rules-family acceptance

A coherent rules family:

1. implements reusable Comprehensive Rules behavior through one typed owner;
2. routes every represented producer through that owner;
3. shares advertisement and command validation while proving both legal-action
   soundness and completeness within the declared supported scope;
4. removes the competing legacy path;
5. lowers applicable Oracle grammar without runtime text parsing;
6. declares precise capabilities and ambient interactions;
7. rejects unsupported variants before mutation;
8. preserves record, replay, projection, and compatibility contracts;
9. improves or holds measured ownership and direct-write debt while keeping
   prohibited card-identity dispatch at zero;
10. regenerates affected rules/card reports once at the final exact head.

Do not select work one numbered rule at a time or preserve a false positive to
avoid a measured demotion. Prefer a subsystem-sized family that removes a
shared blocker and has one reviewable mutation boundary.

The dependency-ready rules batch is not automatically the foreground task.
The generated queue ranks deterministic CI, replay/privacy, missing-owner,
runtime Oracle-text, interaction-assurance, and measured architecture debt
before rules foundations, compiler harvests, and isolated card families. Card
gain breaks ties only inside the same work class. Every serious candidate keeps
its readiness, blocker-card, residual, interaction, ownership, engine, and
runtime-text evidence visible; unknown gains remain unknown rather than being
estimated. [ADR 0062](../adr/0062-cross-program-work-selection.md) records the
durable boundary.

## Invalidation and replay

Rules, Oracle, rulings, compiler, capability, interaction, semantic, and
implementation fingerprints form one trust chain. Changing a source or owner
invalidates dependent evidence until regeneration and tests pass. Game replay
uses the identity pinned at creation; an old record is never reinterpreted as
if it used current rules or card data.

Old data being readable and old behavior being reproducible are separate
claims. A persisted-semantics change needs one focused historical-record
witness for the supported compatibility contract, or an explicit fail-closed
incompatibility. A compiler-version change or current-version replay does not by
itself establish historical compatibility.

## Review hypotheses

Verify a review concern against the current implementation and tests before
changing production. Add the smallest useful reproduction, run it against the
unchanged relevant head, and classify the hypothesis as confirmed, refuted,
intentionally unsupported, or inconclusive. Preserve a useful passing
regression without inventing a defect. Confirmed applicable correctness defects
outrank harvests; unconfirmed static concerns receive bounded triage rather than
blocking independent work. Reports state what was actually inspected, executed,
or reused and never infer completion from an empty artifact, dry-run plan, test
name, or generated row.

## Commands

```powershell
.\.venv\Scripts\python.exe simctl.py rules verify --root .
.\.venv\Scripts\python.exe simctl.py rules coverage
.\.venv\Scripts\python.exe simctl.py rules queue
.\.venv\Scripts\python.exe simctl.py rules next
.\.venv\Scripts\python.exe simctl.py pieces next
.\.venv\Scripts\python.exe scripts\update_rules_scheduler.py --check
```

Run focused evidence during implementation, regenerate full-corpus artifacts at
the final head, and require normal exact-head CI before merging behavioral or
generator changes.

## Claim boundary

Never describe the project as complete Magic or Commander rules support.
Generated status states exactly which snapshot-scoped records, CardPrograms,
capabilities, and profiles close and which blockers remain. Representation,
capability closure, supported composition, safe rejection, and match readiness
remain separate reportable claims. Matchup or deck claims require separate
terminal replay, privacy, legal-action exposure, provider, and sample-
methodology gates beyond rules conformance.
