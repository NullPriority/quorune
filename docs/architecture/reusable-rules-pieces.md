---
title: "Reusable rules-piece inventory"
status: "current"
authoritative_source: "platform/reusable-piece-policy.json and quorune/reusable_pieces"
verified: "2026-08-05"
audience: "compiler, rules, assurance, and architecture contributors"
maintenance: "hand-maintained"
---

# Reusable rules-piece inventory

The reusable-piece inventory is a deterministic classification and inspection
layer over existing rules, compiler, capability, mechanic, runtime, and corpus
artifacts. It does not execute a game, mutate `GameState`, parse Oracle text at
runtime, or create a second trust authority.

It answers which reusable semantic pieces a card consumes, which material
residual families are sole or paired blockers, which cards and repository
owners refer to each piece, which piece interactions occur in the pinned
Commander corpus, and how the current program differs from its durable
accelerator-adoption baseline.

Current counts and fingerprints are generated in the
[matrix](../../coverage/reusable-piece-matrix.md),
[delta](../../coverage/reusable-piece-delta.md), and
[complex-card benchmark](../../coverage/complex-card-composition.md). This
document defines their meaning rather than copying their values.

## Ontology and hierarchy

`platform/reusable-piece-policy.json` owns the versioned ontology. Its classes
cover player and format state, object identity, characteristics, references,
quantities, predicates, actions, proposals, costs and mana, choices and
continuations, stack resolution, typed events and mutations, one-shot effects,
replacement and prevention, triggers, continuous effects, stabilization and
turn structure, combat, card forms, multiplayer Commander, keywords, compiler
nodes, and assurance.

A piece is a stable semantic identity inside one class. Registered capability,
mechanic, compiler-node, compiler-template, runtime-handler, and runtime-
component identities remain distinct pieces and retain links to their existing
authorities. Material frontier families become residual pieces. Card-text-
shaped `unparsed-*` frontier clusters collapse to one shared grammar boundary
per compiler stage; the exact clusters remain drill-down source IDs and are not
misrepresented as reusable primitives. Frontier classification excludes
parenthesized reminder text and quoted granted-ability bodies from the outer
clause, and records composition, prevention, target grammar, and duration as
separate dependency leaves when they are visibly present.

The closed relation vocabulary records intrinsic consumption, production,
observation, modification, granting, removal, replacement, prevention,
redirection, copying, linking, derivation, and profile requirements. A card may
have several relation types to the same piece, but a piece/card/ability relation
is emitted once in canonical order.

## Independent status axes

Status is not one boolean. Every piece reports independent axes for inventory,
compiler lowering, runtime representation, assurance, corpus use, and
interaction evidence. Parsed is not executable; executable is not trusted; a
trusted isolated piece is not interaction-complete; none of those states is a
complete Comprehensive Rules or Oracle claim.

Universal-system summaries conservatively reduce their member classes.
Missing or untested pieces prevent foundation and snapshot-complete claims.
An empty class is inventoried, not complete.

## Inputs and outputs

Generation consumes the pinned Commander card-unlock frontier, capability
registry and evidence fingerprints, mechanic index, runtime handler/component
inventory, rule index, Oracle and CardProgram coverage, architecture audit,
platform snapshots, ontology policy, and the closed declarations in
`platform/reusable-piece-interaction-evidence.json`. Each declaration names an
evidence class, exact test, exact pair or higher-order piece tuple, exact
capability IDs, and the asserted interaction. Sharing a general contract test
does not cover a pair. Official ruling counts join by Oracle ID from the pinned
local database. Ruling prose is not yet behaviorally classified, so ruling
presence is composition evidence, not coverage.

The outputs are a compact matrix, per-card relation index, pairwise interaction
index, complex-card benchmark, durable baseline, and current delta. Every JSON
artifact has a deterministic fingerprint; compressed outputs use canonical
timestamp-free gzip. Validation rejects unknown relations or statuses, stale
source fingerprints, duplicate identities, unclassified material abilities,
stale joins, inflated interaction evidence, and noncanonical generated prose.

The card-unlock frontier stores repeated runtime-component identities in one
sorted dictionary. `quorune.frontier_storage.unpack_frontier_document` expands
the versioned storage envelope before semantic validation or receipt comparison;
historical expanded documents remain readable. Packing preserves every report
field, array order, and semantic fingerprint. Invalid references, ambiguous
fields, unsupported versions, and unused dictionary entries fail closed. The
frontier owner's `--repack` mode validates the current snapshot and rewrites only
this lossless representation, without compiling cards or changing support.

## Inspection and extension

```bash
python simctl.py pieces inventory
python simctl.py pieces coverage
python simctl.py pieces show mechanic.flying
python simctl.py pieces cards mechanic.flying
python simctl.py pieces blockers residual.continuous_layer.continuous-effect-layers-and-dependencies
python simctl.py pieces interactions capability.combat.block.flying
python simctl.py pieces next
python simctl.py card pieces "Storm Crow"
```

Adding a class, relation, or status changes ontology semantics and requires a
policy/schema update plus review. Adding an existing-authority capability,
mechanic, compiler template, handler, or component should appear automatically.
Repeated residual clusters belong in a generic compiler production; they are
never promoted by editing the matrix.

The durable baseline changes only through an explicit snapshot transition.
Ordinary rules and compiler work changes the current matrix and delta while the
baseline remains fixed. Historical baselines are archived rather than silently
reinterpreted after a rules, Oracle, or rulings snapshot change. The baseline
for the active pinned snapshot lives at `coverage/program-baseline.json`.
Before replacing it, preserve the canonical prior file unchanged under
`coverage/program-baseline-history/<baseline_id>.json`, then build the new
baseline from certified `main` against the new snapshot. Feature work on that
snapshot compares with this clean-main baseline rather than resetting its own
delta or comparing different corpora.

## Replay, privacy, and performance

The inventory contains public card names, Oracle IDs, stable semantic IDs,
source fingerprints, test identifiers, and aggregate counts. It contains no
checkpoint, deck order, hand, principal capability, or private decision data.
It does not participate in Game Record v3 replay; it reports replay evidence
attached to existing authorities.

Normal CLI inspection reads generated indexes and never recompiles the corpus.
Full generation is an offline development operation, so authoritative game
latency and state ownership are unchanged.

See [ADR 0022](../adr/0022-reusable-rules-piece-inventory.md), the
[CardProgram boundary](card-programs.md), [compiler boundary](compiler.md),
[trust closure](trust-closure.md), and
[interaction assurance](../testing/interaction-coverage.md).
