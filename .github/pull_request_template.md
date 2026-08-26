# Summary

<!--
CI validates this form. Remove every instructional comment, fill every evidence
row, explain each N/A, and check every safety assertion. Explain the durable
outcome and why this is one coherent change.
-->

## Change class and authority

- Change class: <!-- rules / compiler / replay / persistence / protocol / browser / tooling / documentation / other -->
- Governing rules or capabilities: <!-- IDs, or N/A with reason -->
- Oracle/rulings snapshot: <!-- fingerprint or N/A with reason; do not attach bulk data -->
- Supported profile affected: <!-- profile and scope, or N/A with reason -->

## Ownership and implementation

- Owner before:
- Owner after:
- Duplicate or superseded paths removed:
- `CommanderEngine` delta:
- Direct authoritative-write delta:
- Prohibited identity-dispatch delta:
- Oracle-ID literal delta:
- Compiler/CardProgram changes:
- Card, residual, and capability-closure deltas: <!-- Link generated evidence, or N/A with reason. -->

## Generated base/head evidence

<!-- Paste the exact Markdown block from scripts/pr_evidence.py. CI recomputes it from the PR base and exact head. -->

- Represented family IDs:
- Represented capability IDs:
- Exact head SHA:
- Compiler version delta:
- CardProgram schema delta:
- Exact, trusted, and capability-closed card delta:
- Partial, unresolved, and failed card delta:
- Oracle and CardProgram ability delta:
- Executable trust transitions:
- Structural carrier delta and reconciliation:
- Oracle and CardProgram material residual delta:
- Interaction coverage delta:
- Actual CommanderEngine line delta:
- Reviewed architecture-baseline delta:
- Direct authoritative-write delta:
- Runtime-text delta:
- Printed-name and Oracle-ID delta:
- Production, test, and generated line delta:
- Evidence fingerprint:
- Evidence command:

## Evidence

<!-- Give exact commands/results or N/A with a reason. Documentation-only changes may mark behavior-oriented rows N/A. -->

| Class | Result |
| --- | --- |
| Focused regression and affected module | |
| Multiplayer/APNAP and interactions | |
| Replay, byte/hash, and compatibility | |
| Privacy and capability isolation | |
| Transaction rollback and malformed input | |
| Headless browser and protocol | |
| Property and fuzz | |
| Focused mutation | |
| Compiler/corpus and residuals | |
| Architecture, ownership, and identity flow | |
| Local quick gate | |
| Required exact-head CI | |

## Generated artifacts

- Source inputs changed:
- Generators run:
- Outputs changed:
- Freshness checks:

## Documentation and decisions

- Current documents changed:
- ADR added or superseded: <!-- N/A with reason if no durable decision changed. -->
- Changelog effect:

## Limitations and rollback

- Exact remaining limitations:
- Rollback plan:
- Compatibility or migration risk:

## Safety checklist

- [ ] The change is one coherent subsystem-sized unit; unrelated cleanup is excluded.
- [ ] Advertised actions and accepted commands use the same authoritative legality path, or this is N/A with a reason above.
- [ ] No card-name, collector-number, set-code, or Oracle-ID behavior was added to the generic runtime.
- [ ] No direct `GameState` write was added outside a declared owner.
- [ ] Deterministic replay, protocol/schema versions, privacy projection, and rollback are preserved or explicitly versioned and certified.
- [ ] Generated outputs were regenerated only by their owners and contain no hand-edited metrics.
- [ ] No credential, capability, private hand, library order, checkpoint, live record, bulk archive, database, cache, or artwork was added.
- [ ] Third-party content remains within `docs/LEGAL_CONTENT_BOUNDARY.md`.
- [ ] Required checks were not weakened, bypassed, renamed, or made optional.
- [ ] Every N/A above includes a concrete reason.
