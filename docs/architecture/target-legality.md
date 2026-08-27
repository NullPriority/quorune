---
title: "Target legality and protection"
status: "current"
authoritative_source: "quorune/targets.py, quorune/target_predicates.py, quorune/protection.py, quorune/damage_source.py, quorune/target_protection.py, quorune/target_protection_engine_adapter.py, and CommanderEngine._target_row_matches"
verified: "2026-08-24"
audience: "rules, compiler, replay, and architecture contributors"
maintenance: "hand-maintained"
---

# Target legality and protection

Quorune uses one server-side target predicate for offer generation, submitted
command validation, and resolution-time revalidation. A client receives only
the legal public references in its current action schema and cannot substitute
an authoritative object identifier or bypass the same predicate at commit.

`targets.py` owns the closed schema vocabulary and public-zone boundary.
`target_predicates.py` owns reusable characteristic and relationship tests.
`CommanderEngine._target_row_matches` remains the orchestration facade over
current projected rows and the complete predicate. The narrow
`target_protection_engine_adapter.py` compatibility query materializes the
immutable `TargetProtectionSnapshot`; the pure
`target_protection_verdict` owner evaluates it.

Direct permanent schemas may also carry one typed public-state predicate.
`PermanentStatePredicateSpec` is shared with affected-set queries and evaluates
current tapped state, one named counter minimum, or positive current-turn
battlefield-entry history. Target offers, submitted commands, and CR 608.2b
revalidation all consume that same immutable descriptor. Effective type,
subtype, supertype, keyword, color, and color-cardinality qualifiers remain in
the characteristic snapshot, so represented positive and negative qualities,
including the closed Outlaw exclusion and monocolored or multicolored forms,
respond to copy and continuous characteristic changes without parsing Oracle
text at runtime. Token status remains an authoritative object fact. The same
snapshot supplies one fixed exact,
minimum, or maximum mana-value qualifier. That qualifier observes represented
copy-derived characteristics and the public mana value of a face-down object;
it does not open power, toughness, variable, total, or combined public-state
numeric grammar. The compiler's `DirectPermanentTargetSpec` owner supplies
this one schema vocabulary to counter placement, destruction, exile, and tap
or untap effects; no effect family adds a private legality predicate. Scoped
disjunctions, power or toughness, combat and damage history, unnamed counter
presence, and name or attachment relations remain residual. Ability-presence
wording also remains residual until one shared layer-6 ability applicability
query can serve every static and target consumer.

A target group may additionally require every selected public object to share
one owner. The planner proves that a legal combination exists before it offers
the action, command validation applies the same constraint, and resolution
still revalidates each surviving target independently. The public action schema
emits `same_owner: true` only for such a group, preserving the existing schema
for all other targets. This represents cards chosen from a single graveyard;
it does not expose hidden zones or create a general relational-target grammar.

## Typed protection boundary

The protection snapshot accepts already-derived current facts:

- acting spell or ability controller;
- protected player or permanent controller;
- current represented effective keyword set;
- source colors;
- represented player/controller color protections; and
- the existing typed Protection verdict.

`ProtectionSpec` owns the closed quality descriptor. Schema v1 retains
everything, one color, one represented card type, or Aura. Schema v2 adds one
immutable `ProtectionSourcePredicateSpec` over represented card types, pinned
subtypes, excluded creature subtypes, supertypes, color cardinality, and a
minimum mana value. Multiple printed `and from` qualities remain separate
specifications, so one matching quality is sufficient. The compiler admits
fixed qualities such as Goblins, non-Spirit creatures, legendary creatures,
snow, each color, monocolored, multicolored, and mana value 3 or greater; it
does not grant authority to cast-history, counter-state, modified, chosen, or
other open predicates.

Targeting, blocking, and live attachment legality evaluate that descriptor
against current effective source characteristics. Damage preparation evaluates
the same descriptor against the canonical immutable source snapshot, which now
pins supertypes and mana value alongside type, subtype, and color. A missing
required mana value produces `UNRESOLVED`, and every DEBT consumer fails closed.
No consumer reparses Oracle text or maintains a family-specific quality check.

It never reads or mutates `GameState`, parses Oracle text, chooses a target, or
discovers characteristics. It returns a closed allowed-or-blocked reason.
Malformed controller, keyword, color, boolean, and typed-verdict values fail
before legality is evaluated.

Ordinary permanent Hexproof is controller-relative: an opponent's spell or
ability cannot target the permanent, while its current controller may.
Ordinary permanent Shroud is controller-independent: no spell or ability may
target the permanent, including one controlled by that permanent's controller.
The same current-controller and effective-keyword calculation is repeated at
CR 608.2b resolution revalidation. Shroud, Hexproof, represented Protection,
player protection, and the existing temporary color-qualified
player/controller restriction remain cumulative in the same typed decision
boundary. Non-target selection and attachment legality deliberately bypass
targeting prohibitions and use their own rules owners.

Compiled Aura restrictions still reuse `TargetGroup` for candidate generation
and current characteristic matching. Cast and resolution checks apply ordinary
targeting protection; live attachment checks set the non-target mode and apply
only attachment-specific Protection. The attachment owner records reciprocal
object, public graveyard-card, or player-seat relations, while state-based
actions re-evaluate the same compiled restriction after characteristic,
controller, zone, or active-player changes.

## Compiler, capabilities, and reusable pieces

A source-spanned bare `Hexproof` or `Shroud` keyword lowers through CardProgram
V2 with the fine-grained `target.protection.hexproof_permanent` or
`target.protection.shroud_permanent` capability. Both capabilities depend on
`target.revalidate_resolution` and map to their corresponding capability and
mechanic reusable pieces plus `capability.target.revalidate_resolution` and
`mechanic.cr-115-targets`.

The compiler recognizes ordinary permanent Hexproof and Shroud
case-insensitively and composes them with sibling keyword nodes. It also lowers
the bounded temporary grant "target creature gains shroud until end of turn"
through the same typed characteristic and target-revalidation path. Player
Hexproof, player Shroud, Hexproof from a quality, targeting-exception effects,
multiple or each-quality variants, and rules-text equivalents remain precise
material residuals. Runtime code does not reinterpret those variants.

## Replay, privacy, rollback, and performance

Target schemas persist selected public references and logical identities; they
do not persist a separate Hexproof or Shroud journal. Replay rebuilds the same
current target predicate at offer, command, and resolution boundaries. An
injected illegal reference is rejected before mutation, so the authoritative
state hash is unchanged.

Only the acting principal receives its decision. Hexproof and Shroud use public
current battlefield characteristics and do not expose hidden-zone candidates
or another seat's decision. The pure verdict is constant in the number of game
objects; candidate enumeration remains owned by the surrounding target query.

Primary evidence is in `test_hexproof_targeting.py`,
`test_shroud_targeting.py`, `test_fixed_mana_value_target_predicates.py`,
`test_fixed_direct_target_predicates.py`, `test_oracle_ir.py`, and
`test_capability_implementation_mutations.py`. Broader player Hexproof, player
Shroud, Hexproof-from-quality, effects that ignore either ability, hidden-zone
targets, and unsupported ability-changing, copying, face-down, or merged-object
keyword producers remain outside these trusted slices.
