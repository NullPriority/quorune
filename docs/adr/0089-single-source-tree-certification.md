---
title: "ADR 0089: single source-tree certification"
status: "ADR"
authoritative_source: "CI planner, certification receipt, and main broad regression"
verified: "2026-08-28"
audience: "CI, release, generated-artifact, and assurance maintainers"
maintenance: "hand-maintained"
adr_id: "0089"
decision_status: "accepted"
date: "2026-08-28"
---

# ADR 0089: single source-tree certification

## Context

A high-risk pull request and its content-identical squash merge previously ran
the same complete Linux, Windows, package, generated, and browser matrix twice.
Commit identity could not express that both revisions contained the same
tracked source. A recovery label also authorized a focused fix without proving
which main run, job, shard, or test failed. Conservative generated-owner inputs
could turn unrelated CI tooling changes into a database-backed compiler census.

## Decision

Treat tracked source and generated-output fingerprints as the immutable
certification identity. Every pull-request receipt records those identities,
the evidence run, the required check suite, and one explicit profile:
`complete`, `affected`, `governance`, or `recovery`.

A high-risk pull request runs the complete matrix and publishes a `complete`
receipt. Its content-identical main merge verifies that receipt, skips the
duplicate matrix, and runs only the compact main verification and publication
jobs. An absent, stale, malformed, non-complete, source-mismatched, or generated-
output-mismatched receipt selects the complete main matrix. Manual main-broad
runs always execute the matrix.

Selection-policy changes are high risk unless the base revision's sentinel
proves a structural monotonic addition to the impact policy or test-shard
manifest. Existing rules, assignments, overlays, risks, workflows, planners,
certifiers, and sentinels cannot be removed, moved, or weakened through this
exception.

The `main-red-recovery` label requests examination but grants no authority. A
focused recovery must bind the latest failed main source tree, immutable run and
job IDs, an Ubuntu Python shard artifact, canonical failed test IDs, and an
exact diff limited to those test modules plus their derived generated outputs.
Existing tests and decorators remain present, nonfailing tests remain unchanged,
and failing assertions remain structurally identical. Ambiguous, browser,
product-source, compiler, gameplay, assertion, or missing-artifact failures use
the normal high-risk route.

Generated-owner fanout distinguishes input validation from semantic output
change. An owner marked output-triggered runs its own validation when selected,
but database-backed dependents run only when its committed generated output
changed. Changes to cache identity machinery invalidate cache receipts without
claiming that every semantic generator changed.

## Alternatives

- Run the complete matrix before and after every merge. Rejected because the
  second execution adds no evidence for a content-identical source tree.
- Reuse by PR number, branch, commit message, or squash SHA. Rejected because
  those coordinates do not survive or prove content equivalence.
- Trust a recovery label or changed test path. Rejected because either can hide
  unrelated product changes or weakened assertions.
- Ignore all generated-owner dependencies for tooling changes. Rejected because
  some tooling is a real generator implementation input; the planner instead
  distinguishes validation inputs from changed outputs.

## Consequences

Ordinary pull requests continue to run affected tests plus merge-core and get a
full main matrix after merge. High-risk trees run complete breadth once before
merge and reuse it on main. Invalid reuse fails closed to execution. Main smoke
continues to verify receipt/content equivalence independently.

Required shard results, generated bundles, and certification receipts fail when
publication is absent. Optional metrics and browser reports remain visible but
cannot change semantic certification. Browser behavior, browser-driver setup,
and artifact-publication failures are reported separately.

## Removal condition

Replace this design only if a successor preserves content-bound source and
generated identity, one complete broad execution per equivalent tree, base-owned
monotonic selector review, provenance-verified recovery with assertion
preservation, fail-closed required artifacts, and semantic-output-aware
generated fanout.
