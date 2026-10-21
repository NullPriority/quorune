---
title: "Target legality and protection"
status: "current"
authoritative_source: "quorune/targets.py, quorune/target_characteristics.py, quorune/target_history.py, quorune/target_numeric.py, quorune/object_query.py, quorune/target_predicates.py, quorune/protection.py, quorune/damage_source.py, quorune/target_protection.py, quorune/target_protection_engine_adapter.py, quorune/target_source.py, quorune/target_restrictions.py, and CommanderEngine._target_row_matches"
verified: "2026-10-17"
audience: "rules, compiler, replay, and architecture contributors"
maintenance: "hand-maintained"
---

# Target legality and protection

Quorune uses one server-side target predicate for offer generation, submitted
command validation, and resolution-time revalidation. A client receives only
the legal public references in its current action schema and cannot substitute
an authoritative object identifier or bypass the same predicate at commit.

`selection/target_query.py` resolves an explicit object ID or case-insensitive
public reference before name lookup. A matching reference keeps its identity
even when another object's name matches that string. Zone, owner, controller
and outside-zone filters still apply; a rejected reference does not fall back
to another object's name. Without a reference match, the existing printed and
effective name query retains ambiguity checks. Reference lookup does not
evaluate unrelated objects' continuous characteristics or keep a state cache.

Target snapshots retain the selected object's identity and zone-change counter.
During resolution, `semantic_runtime/direct_target_fields.py` checks that
identity for scalar mutation recipients before `semantic_runtime/values.py`
materializes their arguments. If an earlier instruction moves the target,
the later mutation uses the existing skipped-target path and resolution
continues. A surviving shared target remains usable by later instructions,
including after a shield replaces the first destruction. Informational
references retain their own snapshot, current-information, and last-known
information rules. Casting validation still rejects missing and wrong-domain
selections before payment.

An optional target set may begin with zero chosen objects (CR 115.6). After
validating its descriptor, the homogeneous destruction, movement and tap-state
handler emits the existing empty result plan without object mutation. Later
independent instructions still resolve. This differs from choosing targets
that all become illegal before resolution, where CR 608.2b stops the spell or
ability. Nonempty batches retain their simultaneous owner and strict snapshots.

`targets.py` owns the closed schema vocabulary and public-zone boundary.
Fixed layer-7c P/T queries can select creatures without a represented current
keyword. The shared public predicate retains controller and source-exclusion
scope, and reads completed layer-6 grants and keyword counters. Within continuous
characteristic subjects, negative keywords require an explicit P/T consumer
opt-in; layer-6 grants, cycle-sensitive
count conditions, resolution characteristic sets and fixed counter sets keep
their existing unadopted-keyword boundaries.
`target_cardinality.py` provides an immutable announced-X count descriptor and
a pure binding to exact or up-to integer bounds, including zero. Casting and
typed activation-X owners bind that descriptor before the ordinary target
query and retain the concrete schema with the stack item. The repeated effect
uses the same persisted X as its target limit. An explicit announced-count
selector owns zero and counts above six; legacy fixed descriptors retain their
existing bounds. Dependent public forms follow exact or up-to selection, while
the server revalidates the count and targets. Text-defined counts, unowned
carrier contexts, target roles and linked consequences remain residual.
Target-dependent casting taxes retain their existing source-combination owner;
preview witnesses include the X price needed to select those targets. Exact
selection bounds also follow eligible target count, while up-to selection
leaves the paid X independent of that count. A zero-target cast resolves its
independent atomic results; a spell whose chosen targets all become illegal
does not resolve those results.
The announced-count preview groups equivalent tax-source signatures and prices
each signature once, retaining separate witnesses for payable public targets.
`target_predicates.py` owns reusable characteristic and relationship tests.
`CommanderEngine._target_row_matches` remains the orchestration facade over
current projected rows and the complete predicate. The narrow
`target_protection_engine_adapter.py` compatibility query materializes the
immutable `TargetProtectionSnapshot`; the pure
`target_protection_verdict` owner evaluates it.

Fixed "you have hexproof" and "you have shroud" components derive player
keywords from current effective, trusted battlefield abilities. Their grant
follows the source's current controller and ends when its source leaves or
phases out. Hexproof permits that player's own targeted spells and abilities;
shroud prohibits them as well. Offers, accepted commands and resolution
revalidation use the same snapshot, while untargeted instructions retain their
ordinary recipients. The query introduces no stored player keyword state.

Direct permanent schemas may also carry one typed public-state predicate.
`PermanentStatePredicateSpec` is shared with affected-set queries and evaluates
current tapped state, one named counter minimum, or positive current-turn
battlefield-entry history. Target offers, submitted commands, and CR 608.2b
revalidation all consume that same immutable descriptor. Effective type,
subtype, supertype, keyword, color, and color-cardinality qualifiers remain in
the characteristic snapshot, so represented positive and negative qualities,
including the closed Outlaw exclusion and monocolored or multicolored forms,
respond to copy and continuous characteristic changes without parsing Oracle
text at runtime. Token and face-down status remain authoritative object facts.
A closed public exile target may require `face_down: false`, so a printed
"face-up exiled card" instruction is offered, committed, and revalidated
through the same target owner without exposing a face-down identity. The same
snapshot supplies one fixed exact,
minimum, or maximum mana-value qualifier. That qualifier observes represented
copy-derived characteristics and the public mana value of a face-down object;
it does not open variable or combined public-state mana-value grammar. The
snapshot also supplies fixed at-least or at-most power, toughness, either-value,
or total power-and-toughness predicates for creature targets. The shared exact
numeric boundary includes current continuous and count-derived characteristics,
power/toughness counters, and current-turn stat deltas. A missing or nonintegral
value fails closed when that value is required to prove the comparison; an
either-value predicate may still be proved by its other exact value. The compiler's
`DirectPermanentTargetSpec` owner supplies
this one schema vocabulary to counter placement, destruction, exile, fixed
until-end-of-turn characteristic effects, and tap or untap effects; no effect
family adds a private legality predicate. Scoped
disjunctions, variable, greatest, least, and source-relative numeric values,
unnamed counter presence, and name or attachment relations remain residual.
Ability-presence wording remains outside this target grammar and must use the
shared layer-6 ability-applicability query rather than a family-specific check.

Three positive current-turn damage facts are represented for creature targets:
the current incarnation was dealt damage, dealt damage to any recipient, or
dealt damage to the acting player. The canonical damage transaction records
post-replacement and post-prevention source and recipient logical identities in
the typed turn journal. Zero or fully prevented damage, previous turns, stale
incarnations, qualified sources, blocking relationships, and damage to another
player for dealt-to-you wording do not satisfy the predicate. Offer generation,
submitted-command validation, and resolution revalidation consume the same
typed history query.

A target group may additionally require every selected public object to share
one owner. The planner proves that a legal combination exists before it offers
the action, command validation applies the same constraint, and resolution
still revalidates each surviving target independently. The public action schema
emits `same_owner: true` only for such a group, preserving the existing schema
for all other targets. This represents cards chosen from a single graveyard;
it does not expose hidden zones or create a general relational-target grammar.

## Copied target decisions

Copied spells and abilities retain their selected modes and original target
count in each group. `selection/copy_targets.py` binds those decisions to the
captured original target groups and identity snapshots. Storm and generic copy
choices then use the same structural validator in
`selection/target_validation.py`, and genuinely changed targets still use the
ordinary current target predicate and copied spell or ability controller.

A retained-index selection keeps the original target incarnation even when it
is no longer legal or has left its expected zone. An explicit current-reference
selection chooses the currently represented object and must be legal. The
client exposes these as separate per-slot options, including when a physical
card has left and reentered under the same public reference. A retained target
never receives the new incarnation's snapshot. Legacy default-reference arrays
remain readable as original-target retention; grouped current-reference
selections distinguish new targets. Ordinary casting and unrelated retargeting
receive no copy retention exemption.

Normalized becomes-target occurrences use those committed snapshots as well.
A retained departed incarnation creates no occurrence on a returned permanent
with the same public reference. Choosing that returned incarnation explicitly
does create an occurrence; retargeting compares incarnations before suppressing
an unchanged-target announcement. Copies retain their own stack occurrence and
do not publish a cast event.

The current representation fails closed before copy commitment if one
assignment would require different incarnations sharing the same public
reference, or if a changed relational assignment requires unavailable current
controller facts for a retained nonplayer target. These are explicit
composition limits, not successful approximations. Current-version replay,
descriptor readability and historical execution remain separate claims; old
records require matching execution provenance and are rejected when that
provenance is incompatible.

## Fixed source-specific prohibitions

`target_restrictions.py` evaluates compiled static prohibitions through the
same target predicate as offers, commands and resolution. Closed affected sets
cover the source, an enchanted creature, creatures, controlled creatures, and
actual cards in public graveyards. Predicates distinguish spells, abilities,
fixed source colors, artifact sources and Aura spells. They restrict targeting;
ordinary damage, blocking, attachment and untargeted instructions retain their
own rules. Effective static-component presence controls these effects, including
copied sources, ability removal, controller changes and phasing out.

`target_source.py` supplies immutable acting-object facts. A spell uses its
selected face's effective characteristics. An ability retains its own locked
controller separately from its source's current controller and characteristics.
Source-qualified controller predicates evaluate the source; controller predicates
on the ability evaluate the ability. A shared opponent qualifier applies to
both spells and abilities. For a worded Aura prohibition, its controller is
the observer even when another player controls the enchanted creature; this
does not grant the ordinary Hexproof keyword. Incarnation-bound departure capture retains
immediate last-known source information. A returned physical card cannot replace
that source. Missing required information fails closed.
Each target-candidate query captures its immutable effective prohibition inputs
once for that group. The next query captures them again; no mutable game-state
cache or issued action can preserve an earlier source condition.

Targeted activation announcement uses the ordinary stack owner before paying
costs, as required by CR 602.2a. Selected-object costs and mana payments therefore
share canonical departure capture. Payment finalization preserves that source
frame and adds paid-object information. Failed or suspended commands roll back
the announcement with their costs; replay and continuation resumption create one
accepted frame. No transaction snapshot is client authority.

The compiler retains fixed spell/ability restrictions and an attached fixed
stat modifier in one source-spanned node when printed together. Conditional
attack-history restrictions, Wall-only schema introspection and qualified
Hexproof keyword grammar and instant or sorcery static sources remain outside
this production.

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

Schema v3 adds a fixed-color or fixed-card-type, per-instance exception for the
granting Aura.
Only a live attached definition may carry its unbound source marker; the
existing attached handler binds that marker to the current Aura incarnation.
Current attachment retention may ignore that one instance for that Aura.
Another protection instance still applies, and targeting, damage and blocking
receive ordinary protection. The exception does not permit a new attachment
or ignore the Aura's Enchant restriction. All-Aura and already-attached
exceptions remain outside this production.

The fixed-card-type compiler production admits protection from enchantments,
optionally alongside a signed power/toughness modifier. Its separate capability
also participates in the runtime minimum for the existing attached handler.
The exception changes only current attachment retention. An enchantment-source
ability still cannot target the protected creature, even when its sacrifice
cost would remove that protection after target selection.

Source-chosen Aura protection uses a separate closed continuous component. The
existing entry-designation owner records one public color for the entering
Aura's controller. The component reads that retained source value and emits
the same fixed-color schema-v3 grant for the current attached creature. A
different creature controller does not choose or receive the Aura's entry
draw. Return ends the grant, and a new entry selects again for the new Aura
incarnation. Characteristic queries never prompt, mutate the designation or
interpret Oracle text.

Targeting, blocking, and live attachment legality evaluate that descriptor
against current effective source characteristics. Damage preparation evaluates
the same descriptor against the canonical immutable source snapshot, which now
pins supertypes and mana value alongside type, subtype, and color. A missing
required mana value produces `UNRESOLVED`, and every DEBT consumer fails closed.
No consumer reparses Oracle text or maintains a family-specific quality check.

Represented temporary Protection grants use that same typed fragment in the
resolution-created layer-6 journal. Fixed color and artifact qualities, or a
color selected by the resolving controller or the target's current controller,
therefore apply through the ordinary targeting, blocking, attachment and damage
consumers. The selected quality and affected incarnation remain fixed until
cleanup; later entrants and returned incarnations do not inherit the grant.

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
case-insensitively and composes them with sibling keyword nodes. Qualified
Hexproof retains separate closed keyword values for fixed colors, each color,
monocolored or multicolored sources and single represented card types. The
printed-face bridge preserves those values instead of a collapsed bare
Hexproof index entry. An independently printed or later granted plain Hexproof
ability still applies to all opposing sources.

The pure target verdict consumes the existing typed acting-source facts. A
spell's current characteristics, or an ability source's current or immediate
departure information, supply its qualities. The ability controller supplies
the opponent relationship even after its source changes controller. A returned
incarnation cannot replace the original source information. Colorless sources
are neither monocolored nor multicolored. Matching qualities affect targeting;
they do not prevent blocking, damage or untargeted effects.

Shared keyword queries treat a qualified variant as Hexproof. Removing or
prohibiting Hexproof removes every variant; later grants and independent plain
Hexproof follow the ordinary layer-6 owner. Existing typed grants and removal
carry these closed values through the same target-revalidation path. Fixed
player keywords use their separate components described above. Unrepresented
chosen qualities, compound type lists, targeting exceptions and rules-text
equivalents remain material residuals. Runtime code consumes compiled values.

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

Primary evidence is in `test_qualified_hexproof.py`, `test_hexproof_targeting.py`,
`test_shroud_targeting.py`, `test_fixed_mana_value_target_predicates.py`,
`test_fixed_damage_history_targets.py`,
`test_fixed_direct_target_predicates.py`, `test_oracle_ir.py`, and
`test_capability_implementation_mutations.py`. Broader player keyword grants,
effects that ignore either ability, hidden-zone targets, and unsupported
ability-changing, copying, face-down, or merged-object
keyword producers remain outside these trusted slices.
