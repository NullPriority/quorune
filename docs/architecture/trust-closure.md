---
title: "CardProgram trust and applicable closure"
status: "current"
authoritative_source: "quorune/card_programs/trust.py, binding.py, preflight.py, and the versioned capability registry"
verified: "2026-08-05"
audience: "rules, compiler, runtime, replay, and room-policy contributors"
maintenance: "hand-maintained"
---

# CardProgram trust and applicable closure

Trust is an executable, fingerprinted contract rather than one broad boolean.
The trust code owns no game state and performs no mutation. It consumes pinned
CardPrograms, the capability registry/evidence index, the selected profile,
registered handlers/components, and any dynamic capabilities; it produces
deterministic intrinsic, format, match, and dynamic closure reports.

## Trust bases

Every CardProgram V2 reports exactly one basis:

- `capability_closed`: every represented material ability has a current trusted
  intrinsic capability closure and no hidden compatibility-only path;
- `legacy_reviewed`: execution relies on source-pinned reviewed semantic-pack
  behavior whose fine-grained closure is incomplete;
- `mixed`: both bases occur in one card;
- `provisional`: behavior exists but its assurance is incomplete;
- `unresolved`: a material residual, stale source, arbiter dependency, or
  blocked capability remains;
- `non_rules_governed`: the represented text is intentionally outside the
  deterministic traditional-rules profile.

Legacy-reviewed behavior can keep the declared pool operational through the
explicit compatibility policy. It is never reported as capability-closed.
Compatibility provenance pins the source hashes, stable card/ability and
handler/component identities, tests, replay identity, and removal condition.

## Closure layers

The intrinsic layer is derived from the card's costs, targets, effects, event
handlers, and runtime components. The format layer represents capabilities
always active for the selected rules profile. Match closure conservatively
unions all loaded programs and known format dependencies. Dynamic closure
binds capabilities introduced during play.

The current fine-grained registry does not yet inventory the complete
traditional or Commander format layer. Consequently capability-only strict
match readiness reports
`format_profile:capability_inventory_incomplete:<profile>`. Reviewed
compatibility remains separately available. This fail-closed blocker is
intentional; an empty format declaration is not proof that CR or Commander
ambient rules are complete.

Deck-level strict closure also reports every missing CardProgram, legacy or
mixed trust basis, and unbound per-card runtime dependency. The bounded
Commander match-readiness reference set keeps those categories separate from
format inventory and from reviewed-compatible execution; removing one category
does not silently promote the match while another remains.

Unrelated blocked registry entries do not block a match. A blocked capability
does block when it enters the match or dynamic reachable set. Conservative
overblocking is allowed; underblocking is not.

## Runtime binding and replay

Strict binding compares every declared ability closure to the current registry
and evidence fingerprints, then adds capability dependencies declared by each
registered semantic handler and runtime component. Missing declarations,
handler schema/event drift, untrusted closure, or a fingerprint mismatch fail
closed. The global inventories are metadata aggregations; family registries
remain the execution owners.

New Game Record v3 manifests pin capability registry/evidence, semantic-handler,
runtime-component, CardProgram, and CardProgram-trust fingerprints. A command
that resolves a semantic program also records a compact binding fingerprint and
the handler/component IDs used. Exact replay recomputes and compares them.
Historic v3 records without these additive fields retain their prior semantic
fingerprint verification.

Primary tests are `test_card_program_trust.py`, `test_preflight_v080.py`,
`test_game_record_v3.py`, and `test_semantic_searches.py`. Capability evidence
and mutation declarations are checked independently; see the
[capability guide](../extension/mechanic-capability.md),
[runtime-component architecture](runtime-components.md), and
[mutation testing guide](../testing/mutation.md).

Trust computation is deterministic metadata work. It is performed at compile,
preflight, load, and replay boundaries rather than inside characteristic hot
paths.
