---
title: "Oracle compiler architecture"
status: "current"
authoritative_source: "quorune/oracle_ir.py, quorune/compiler, and quorune/card_programs"
verified: "2026-09-29"
audience: "compiler and rules contributors"
maintenance: "hand-maintained"
---

# Oracle compiler architecture

The compiler transforms a pinned local card and rulings snapshot into typed
Oracle IR, recognized semantic nodes, dependency declarations, material
residuals, and a canonical `CardProgram`. For the same inputs and compiler
version, the result must be deterministic.

```mermaid
flowchart LR
    Input["Pinned card and rulings"] --> Normalize["Normalize faces and text"]
    Normalize --> Parse["Parse typed spans"]
    Parse --> Lower["Lower exact constructs"]
    Parse --> Residuals["Classify residuals"]
    Lower --> Gate["Bind trust and dependencies"]
    Residuals --> Gate
    Gate --> Program["Canonical CardProgram"]
```

## Stage ownership

`oracle_ir.py` owns parsing and IR compatibility.
`compiler/program_generation.py` owns lowering exact nodes into registry
programs. `card_programs/adapters.py` combines abilities, face identity,
source hashes, residuals, and capability closure into the canonical artifact.
For one CardProgram compilation, the adapter passes its locally compiled IR to
the existing program-generation lowering owner. Lowering still validates trust
and constructs fresh semantic programs; it does not parse the same record a
second time. The standalone generated-program API continues to compile its own
IR. No IR result persists across calls or card records.
The frontier owner passes the same original IR used for its node inventory to
CardProgram lowering, retaining independent trust and construction-failure
checks while avoiding a second parse of the same record.
The governed corpus writer uses one pass over the full pinned database to feed
the canonical Oracle and CardProgram report counters. It obtains Commander
membership from the same database iterator used by standalone coverage commands
and feeds those records to separate Commander counters. Each view preserves its
own source order, examples, target-effect assurance, trust/residual counts,
construction failures, and snapshot identity. A failed CardProgram remains a
failure in its applicable views without removing the card's Oracle evidence.
The local card database is a compiler input; the engine does not query it while
performing a transition.

Source-self action, zone-change, source-plus-other, and graveyard trigger parsers
first check a name-independent superset of their closed event grammar. Only
possible candidates construct a source-name regex. The original escaped-name
matcher still determines recognition, captured subjects/events/bodies, and
serialized binding; the prefilter never promotes grammar or supplies runtime
authority. This avoids compiling a distinct name-bearing pattern for unrelated
Oracle lines while preserving full source spans and residual boundaries.

`compiler/linked_exile_return_templates.py` lowers a closed battlefield exile
followed by an immediate return or next-end-step return. It shares direct
permanent predicates, optional resolution choices, modal scoping, and ordinary
activated/triggered shells. The paired instruction carries one binding, fixed
owner-or-resolver control, tapped state, represented entry counters, and an
optional immediate fixed keyword grant. Exiled tokens cannot return. Conditional
outcomes, transformed or face-down returns, source-departure banishment,
attachment restoration, and unsupported riders remain residual.

`compiler/fixed_target_effect_sequences.py` owns the closed cross-sentence
target-threading grammar. It is the only compiler authority for represented
fixed counter plus until-end-of-turn characteristic sequences: one clause
establishes target zero through one closed `DirectPermanentTargetSpec`, later
clauses use the exact pronoun “it,” and printed operation order is retained.
Standalone and target-first fixed characteristic effects use that same target
descriptor, including represented type, subtype, supertype, color, controller,
combat-state, and source-exclusion predicates. Open target predicates, dynamic
results, optional or multiple targets, and target-changing effects remain
residual rather than falling back to a creature-only approximation.
The runtime consumes only the resulting typed node and never reparses those
Oracle sentences.

`compiler/temporary_target_interactions.py` composes that same direct-target
descriptor with signed spell-cost X, supported comma-separated keyword lists,
fixed color or artifact Protection, a choice of color or two supported keywords,
a closed current-target conditional rider, or a fixed regeneration or
unblockable tail. It keeps the original fixed leaf-parser boundary intact.
X requires an X-bearing spell mana cost on the selected face; undefined X,
variable activated costs and other derived quantities remain residual.
Resolution choices use the existing scalar-choice owner. A recipient-controller
color choice reads the target's current controller when the instruction begins.
Conditional riders evaluate current public characteristics once before creating
the locked layer-6 or layer-7c effect. The same continuous-effect journal stores
temporary Protection as a typed fragment, and existing DEBT consumers enforce it.
Open conditions, unsupported qualities or keywords, target-changing and
multiple-target effects, other durations and must-be-blocked requirements remain
residual. Existing carrier and independently closed composition owners still
govern triggered, activated and modal admission.

`compiler/fixed_source_effect_sequences.py` owns the separate source-threaded
two-clause grammar. It accepts one fixed positive counter placement on a
permanent source followed by one represented fixed characteristic result until
end of turn. Both instructions lower to `$source.zone_object`; runtime
resolution validates the same physical and logical battlefield incarnation
before counter replacement and again when a suspended continuation resumes.
This production contains no card names or mechanic-specific runtime behavior.

`compiler/fixed_controller_effect_sequences.py` owns one closed two-instruction
controller grammar across spell, triggered, and activated contexts. It requires
exactly one positive fixed ordinary draw plus one represented fixed controller
life change, Scry, or private discard of one through four cards, in either
printed order. The controller-discard leaf exists only inside this complete
sequence and reuses the affected-player private selection and simultaneous
replacement-aware move owner. Targets, affected-player groups, random or
optional discards, dynamic quantities, conditional or linked results, and
larger sequences remain source-spanned residuals or route through a different
complete composition owner.

`compiler/fixed_counter_controller_effect_sequences.py` owns a third closed
two-clause grammar. It accepts exactly one fixed counter placement on the
current source zone object or one direct permanent target plus exactly one
fixed controller draw, life-change, or Scry instruction, in either printed
order. Each clause reuses its existing typed owner; the sequence adds only the
immutable ordering and continuation boundary. Optional, modal, conditional,
variable, linked, repeated, and larger instruction families remain residual.

`compiler/delayed_draw_templates.py` owns one mandatory delayed controller
draw instruction: draw one card at the beginning of the next chronological
turn's upkeep. The compiler emits one immutable single-use delayed-trigger
payload containing the current turn sequence and one private controller draw.
The ordinary delayed-trigger scheduler, APNAP placement owner, and draw
transaction retain runtime authority. The same leaf is available to spell,
triggered, and activated composition, but it is withheld when an unresolved
additional cost or other whole-ability owner encloses the sentence. Optional,
multi-card, variable, controller-next-upkeep, named-player, other-time,
conditional, repeated, linked, and targeted variants remain source-spanned
residuals.

`compiler/life_templates.py` owns fixed life changes shared by spell and
activated contexts. In addition to controller gain/loss, it lowers one direct
player gain or loss, one opponent-only loss or equal drain, and one equal
each-opponent drain. Direct targets use the ordinary public player-target and
resolution-revalidation boundary; table-wide drains use one canonical life
batch. Dynamic amounts, target-player drains that could affect the controller,
each-player loss, life-total setting, exchanges, and unequal drains remain
source-spanned residuals.

`compiler/mill_templates.py` owns one mandatory positive fixed-count Mill
instruction for the controller, one target player, or one target opponent.
The same grammar is consumed by spell, triggered, and activated contexts and
feeds one immutable current-library-top plan into the canonical simultaneous
zone-transition owner. A short library mills as many cards as possible;
destination replacements, actual public results, target revalidation, APNAP
zone triggers, rollback, privacy, and replay remain shared. Optional and cost
Mill, dynamic or half-library quantities, player groups, linked
cards-milled-this-way consumers, and graveyard-order-sensitive interactions
while CR 404.3 is blocked remain source-spanned residuals or explicit trust
exclusions.

`compiler/surveil_templates.py` owns one mandatory positive fixed-count
controller Surveil instruction. The leaf grammar is shared by spell,
self-entry trigger, activated, and independently typed two-clause contexts.
It issues the generic private ordered-partition choice and delegates selected
library-to-graveyard cards to the canonical simultaneous zone-transition owner;
runtime never reparses the printed instruction. Zero, dynamic, optional, cost,
targeted, repeated, copied, granted, additional-look, linked-result, and
unsupported Surveil-event trigger forms remain source-spanned residuals.

`compiler/fixed_library_selection_templates.py` owns complete fixed positive
top-of-controller-library instructions that select cards into hand and send
the complete remainder to the graveyard or library bottom. The same leaf
composes through spell, triggered, activated, and modal contexts. It emits
only closed fixed, up-to, all-matching, or optional-slot cardinality plus typed
characteristic predicates; the private choice handler and immutable partition
owner retain selection, revalidation, replacement, ordering, visibility, and
replay authority. Variable counts, another player's library, numeric or named
predicates, conditional or payment grammar, other destinations, and sibling
effects remain source-spanned residuals. See
[ADR 0091](../adr/0091-typed-fixed-library-selection.md).

`compiler/entry_state_templates.py` also owns one closed family of fixed public
conditions that determine whether a land enters tapped. `FixedEntryCondition`
uses a typed metric, integral bound, and tap polarity for controller land and
basic-land counts, individual basic land subtypes, aggregate opponent lands,
minimum active-player life, and represented controller permanent queries. The
zone replacement snapshot evaluates only already-present phased-in battlefield
permanents through the canonical effective-characteristic boundary, so neither
the entering land nor another member of the same simultaneous entry batch can
feed its condition. Turn-relative, variable, chosen, hidden, counter-qualified,
history-sensitive, and unsupported same-layer characteristic predicates remain
source-spanned residuals.

`compiler/fixed_effect_clause_sequences.py` owns the general closed
two-sentence composition boundary. It accepts exactly two top-level,
period-separated clauses when each clause independently lowers to
one effect through an existing reviewed atomic owner and the pair contains at
most one target schema. Direct targets and bounded optional target sets are
associated with their component through typed target references; every other
component must remain nontargeted. The composed node preserves printed order
and the exact union of both component capabilities across spell, triggered,
and activated contexts. One clause may use the fixed optional-effect owner
described below; its choice wraps only that clause, so a mandatory sibling
remains outside the choice. Modal, conditional, linked-result, pronoun,
variable, repeated, independently targeted, unsupported optional, quoted-boundary,
parenthetical-boundary, and larger sequences remain source-spanned residuals.

`compiler/resolution_condition_templates.py` composes one fixed public-state
conditional result with an optional mandatory typed prefix. It preserves each
component's mechanics independently and checks the condition only when its
printed instruction is reached. The shared public-query owner binds “you” to
the resolving spell or ability controller, even when its source has left or
changed controller. Only nontargeted results and independently closed prefixes
are admitted. Bare subtype-presence conditions retain the battlefield permanent
domain, including represented noncreature Kindred objects; an explicit creature
noun retains its creature type restriction. Source-relative, attached-object,
targeted-result, nested,
alternative, linked-result and “instead” forms remain residual. Derived-quantity
and source-relative result forms, plus delayed results, remain residual inside this wrapper until
their conditional read timing is represented; their unconditional owners remain
available. Existing typed
leaf validators also participate in the common closed-component gate; no leaf
grammar or mutation owner is duplicated.
The mandatory delayed-draw leaf retains its separate temporal scope and is not
lifted into optional or closed multi-result programs by this component gate.

`compiler/fixed_homogeneous_target_sets.py` lifts an existing reviewed scalar
destroy, exile, tap, untap, battlefield-return, own-graveyard-return, or public-
graveyard-exile clause into one interchangeable public target set. The grammar
accepts exact counts from two through six, one-or-two, and up-to one through
six. Battlefield returns reuse every closed direct-permanent predicate;
own-graveyard returns reuse fixed card types, type intersections and unions,
subtypes, supertypes, colors, and color cardinality. Qualified permanent-card
predicates retain that explicit card domain when the battlefield parser's
permanent zone would otherwise make it implicit. The owner adds only one
shared count and resolution-revalidation capability, and supports a single-
graveyard constraint through target ownership. The same scalar leaves also feed
the existing optional and closed-sequence compilers. Heterogeneous roles,
divided or dynamic quantities, linked results, compound or conditional tails,
other players' graveyards, non-owner-hand destinations, and unsupported
characteristic dependencies remain source-spanned residuals.

Public quantity conditions reject keyword-qualified existence queries before
constructing their closed quantity descriptor. An unsupported condition keeps
its source-spanned residual; a successful direct-target predicate does not
imply that the narrower quantity owner can evaluate it.

`compiler/public_state_queries.py`, consumed by
`compiler/continuous_templates.py`, lowers fixed controlled permanent sets that
gain supported keywords, or gain fixed power/toughness and supported keywords,
until end of turn. `compiler/fixed_public_characteristic_sets.py` extends that
resolution owner to all creatures, creatures opponents or one target player
control, current attacking or blocking creatures, and source-excluding attacking
creatures. Both grammars feed one closed query validator. Resolution evaluates
only cycle-safe current type, subtype, color, supertype, token, controller, and
public combat-state facts, then locks the matching logical objects. The runtime
sends ability additions through shared layer 6 and power/toughness changes
through layer 7c over that same locked set. The source leaving or changing
controller does not end the effect, later entrants do not join it, returned
objects are new logical objects, and cleanup expires both layers. Multiple or
opponent-only targets, attachment-relative, chosen, counter-qualified, tapped,
modified, keyword-qualified, dynamic, conditional, quoted, Protection,
type-changing, and variable-duration forms remain source-spanned residuals.

`compiler/fixed_target_effect_sequences.py` also owns one fixed source-
characteristic result shared by activated and normalized triggered contexts.
It accepts a fixed power/toughness modifier plus supported keywords, a fixed
set of two supported keywords, a colorless or all-colors source change, or a
fixed artifact animation with literal colors, creature subtypes, base
power/toughness, and supported keywords. The runtime commits every represented
layer-4, layer-5, layer-6, layer-7b, and layer-7c component atomically with one
timestamp and one locked source incarnation. The same canonical keyword map
extends targeted spell and activated effects without a family-local ability
check. Layer-4 animation feeds the existing cycle-safe dynamic-characteristic
boundary before layer-7a counts, and the state-based owner detaches Equipment
that becomes a creature. Dynamic or
chosen values, copies, text or control changes, declaration riders,
Protection, landwalk, Banding, unsupported keywords, and non-until-end-of-turn
durations remain source-spanned residuals.

The same resolution-characteristic owner accepts a closed version-2 fixed
animation or base-setting instruction. `compiler/fixed_resolution_characteristics.py`
preserves source-incarnation, qualified direct-target, or represented fixed
public-set selection. Its typed value distinguishes replacing types from
retaining prior types and creature subtypes, and can set literal colors and
base power/toughness, remove all abilities, and add supported keywords with
one end-of-turn timestamp. An absent creature-subtype instruction preserves
prior creature subtypes; replacing card types removes the subtype sets
correlated with lost types under CR 205.1a. Unqualified artifact-creature
wording retains all prior types and subtypes, while a specified creature subtype
replaces only creature subtypes under CR 205.1b. An explicit "still" or
"in addition" rider retains all prior subtype sets. Existing version-1 source
descriptors retain their historical operation path. Copy, dynamic/chosen values, text/control,
attached-object subjects, declarations, quoted grants, conditional/optional
instructions, unsupported keywords, and other durations remain residual.
An actual pre-extension action record is rejected at the existing versioned
runtime-trust boundary before reinterpretation; preserved v1 instruction
execution and current exact replay are separate tested claims.
Fixed base setting does not erase counters or layer-7c modifiers. Resolution
membership excludes phased-out permanents and is locked to current logical
objects, so later entries and new incarnations cannot join the effect.

The existing attached-characteristic handler in
`compiler/continuous_templates.py` lowers closed enchanted, equipped, and
fortified subject wording into one versioned live-relation descriptor. The
grammar covers fixed and typed public-query power/toughness, base-setting,
type, subtype, color, and supported keyword addition or removal; compound
forms remain one descriptor so canonical layer ordering is preserved. The same
descriptor may append one or two already-exact declaration requirement or
restriction fragments, including beside a fixed attached modifier and keyword.
Typed quantities reuse `query_characteristic_quantity()` and the cycle-safe
layer-5 object query. Name and rules-text changes, conditions, declaration
costs, target-relative attachment or controller facts, chosen values, and
untrusted granted abilities remain source-spanned residuals. Witness Protection
and Retro-Mutation are explicit examples of those exclusions.

The quoted-ability compiler also accepts one independently exact activated,
fixed-output mana, or triggered body behind a closed live battlefield query.
The attached grammar includes enchanted lands, and fixed live-query subjects
include controlled tokens, Foods, Treasures, Clues, and basic lands. Both the
query and attached forms emit a separately keyed inner CardProgram and
a typed layer-6 fragment; the runtime never reparses the quote. The fragment
preserves only activation costs already committed by the canonical activation
transaction, including fixed mana, tap or untap, source sacrifice, life
payment, selected public objects, and the represented restricted-mana profile.
Multiple quotes, quoted static or declaration prose, named or external source
references, hidden-zone recipients, and independently inexact bodies remain
material residuals. Granted fixed source-counter removal costs also remain
residual until the granted-ability fragment schema can carry that typed cost.

`compiler/conditional_granted_ability_templates.py` wraps one independently
exact quoted child with the existing closed public-state characteristic
grammar. It retains fixed coupled keywords and power/toughness modifiers in
the same condition. A distinct schema-v2 runtime handler emits the typed
fragment through the shared layer-6 owner; the earlier characteristic-only
schema remains strict. An unsupported condition, quoted child, nested static
grant, multiple quotation, or material sibling prevents complete-card closure.

Fixed static declaration composition reuses the declaration requirement and
restriction parsers rather than adding a combat validator. A source-local line
may contain either one exact fragment or one of the closed two-fragment
conjunctions. Public-query and attached forms grant the same fragments through
their existing layer-6 owners, optionally beside an exact public keyword or
fixed attached characteristic prefix. Fixed hand, graveyard, battlefield,
counter, attachment, draw, cast, and current-stat conditions reuse the same
public-state snapshot and cycle-safe characteristic boundary as conditional
static components. Exact attached restraints may also add one all-ability or
nonmana activation-prohibition fragment through that shared applicability
query. Source-controller recipient restrictions stay anchored to the
originating source after the affected creature receives its characteristics.
Offer and commit consume the same current fragment snapshot. Nonmana costs,
chosen or named values, temporary mass effects, crew and transform riders,
unsupported previous-turn facts, open dynamic counts,
multiple-block capacity, and combat-damage assignment remain material
residuals. See [ADR 0099](../adr/0099-typed-public-declaration-conditions.md).

`compiler/defender_permission_templates.py` separately compiles the closed
permission to attack as though a creature did not have Defender. It preserves
Defender and grants one typed layer-6 fact to the current self, attachment or
public-query recipient. Closed public-state conditions use the existing
snapshot owner through a strict permission-only schema. Shared attacker
legality disregards only Defender; tapped state, summoning sickness and other
attack restrictions still apply. Fixed temporary self, target and public-query
forms lock current logical incarnations through the ordinary end-of-turn
continuous-effect journal, optionally sharing one timestamp with fixed stats
or supported keywords. Later entrants and new incarnations receive no earlier
temporary permission. Linked riders, defending-player history, and
combat-damage assignment rules remain separate unsupported families.

`compiler/as_unblocked_templates.py` owns the separate printed instruction
to assign combat damage as though a creature were not blocked. Intrinsic,
attached, public-query and closed source-conditioned forms grant one typed
current layer-6 permission. The existing combat snapshot and assignment
proposal own its ordinary versus all-recipient choice, including Trample,
attacked planeswalkers and Battles, and independent first-strike damage steps.
Quoted temporary ability grants lock current recipients to their incarnations.
Unquoted optional assignment instructions instead use the existing resolution
choice. Accepting the controller-set form creates a duration-bound rule that
requires all current controlled attackers to assign as unblocked, including
later entrants; it neither grants an ability nor freezes the creature set.
Both forms use the existing duration journal. Variable target counts, unrepresented
assignment controllers, Banding and independently unsupported siblings remain
residual. This family does not remove blocked status or redirect damage to
the attacked permanent's controller.

`activation_condition_model.py` owns complete trailing activation
restrictions shared by the compiler-pinned catalog and activation lowering.
It represents controller upkeep and pre-attack turn windows plus fixed-range
controller battlefield, graveyard, and identity-free hand queries. The
runtime activation-condition owner evaluates battlefield queries against full
current effective characteristics, including the common layer-6 ability
applicability path, while hand conditions inspect only zone size. Offer and
commit call the same condition owner, and commit revalidation occurs before
mana, tap, usage, source movement, or stack mutation. Opponent, target-player,
attachment, counter, tapped/combat, chosen, named-card, distinct-value,
total-power, source-relative, historical, compound, and unsupported timing
forms remain source-spanned residuals.

The same compiler owner separately lowers fixed static characteristic bodies
gated by one closed public-state condition. A versioned descriptor combines a
source-controller turn, fixed public hand or graveyard query, life total,
current-turn entry, named source counter, source or attached-object public
state, cycle-safe controller or opponent layer-5 quantity, canonical current-
turn draw or spell-cast count, opponent poison count, or controller monarch
designation with the existing source, attached-object, or fixed-query target
grammar. Runtime snapshots only authoritative state through the shared query,
draw, turn-history, player-counter, and designation owners, then sends paired
ability additions and power/toughness modifiers through the same applicability
path. Attachment-relative “it” binds to the explicit enchanted or equipped
object; other source pronouns remain source-bound. Public state may participate
in layer-5 quantities, but positive ability presence remains excluded from
counts. Schema-v4 facts add cycle-safe distinct card-type and exact mana-value
sets from the controller's graveyard; sealed entry, attack, sacrifice, death,
and life history; public hand and starting-life relations; planeswalker subtype
queries; and exact Equipment attachment counts. Printed numerical thresholds
remain explicit descriptor amounts. Graveyard colors, card types, and canonical
creature subtypes retain distinct query axes. Normal and token entry producers
record the current transition before trigger discovery, and attack history seals
effective type and subtype membership plus the attacked object class while
counting distinct attacking logical objects. Schema-v3 public facts remain a
  historical compatibility form whose attack quantity counts recorded attack
  occurrences.
  The same descriptor also represents any creature death, the controller's
  attack or permanent departure, an opponent's life loss, and fixed total
  life-gained/lost thresholds this turn. These are sealed historical facts,
  not current object counts. Departures use the previous controller and
  incarnation after committed replacement-adjusted movement; prevented
  movement supplies no fact. New games enable departure recording explicitly,
  while older journals leave that fact unavailable. Intervening-if triggers
  evaluate the shared descriptor at occurrence and again under their locked
  trigger controller at resolution. Descend remains a distinct unsupported
  all-zone permanent-card history domain.
Most-common or tied comparisons, dynamic amounts, top-library, chosen,
hidden-identity, city blessing, dungeon, initiative, speed, sticker, crime,
dice, open arithmetic, otherwise branches, per-opponent aggregate, unowned
history/designation, quoted-ability, type, color, ability-removal, Class-level,
Protection, Ward, and unsupported-keyword forms remain source-spanned
residuals. See
[ADR 0087](../adr/0087-typed-fixed-public-state-characteristics.md).

`compiler/closed_effect_programs.py` owns the broader bounded composition
boundary for two to four mandatory components. It partitions only top-level
sentence, comma-then, and conjunction boundaries, requires every component to
lower through an existing reviewed typed owner, flattens their effects in
printed order, and permits at most one shared direct target group. The
corresponding capability shape revalidates every flattened component through
the existing effect capability resolvers before admitting the program. The
older exact two-sentence owner retains precedence for historical semantic
identity. Independently exact optional atomic components may use the fixed
optional-effect owner without changing sibling scope. Conditional, random,
repeated, variable, linked-result, unsupported optional,
shared-subject, multi-target-group, quoted-boundary, parenthetical-boundary,
and more-than-four-component programs remain source-spanned residuals.

`compiler/bound_effect_programs.py` owns the complementary single-reference
composition boundary. Two through four mandatory components may share one
player subject or reuse one explicitly established player or battlefield target
through “it,” “that player,” “that creature,” or “that permanent.” It retains
the original target qualifiers and source exclusion, verifies reference-domain
compatibility, and emits the existing effects in printed order. Separate
explicit target occurrences never become one target merely because their
schemas match. The bound-reference capability checks every component against
its typed owner; execution, replacement choices, private affected-player
choices, and checkpoint continuations remain in their existing owners. Owner
or controller possessives, cross-zone or hidden-object references, unsupported
leaf grammar, conditional or optional linkage, and derived results remain
residual. Whole-program integration may admit these bounded combinations while
the original unbound leaf composition correctly continues to reject them.

`compiler/optional_effect_templates.py` owns one leading `You may` around one
independently exact atomic effect. It preserves the nested effect's target
schema and mechanic dependencies, adds only the generic controller choice,
and is shared by spell, normalized-trigger, activated, and bounded sequence
contexts. Existing specialized optional owners retain precedence and semantic
identity. Optional costs, permissions, replacements, pay-or-have wording,
modal or repeated choices, linked or conditional results, multiple effects in
one optional body, and bodies without an independently exact typed owner remain
source-spanned residuals.

`compiler/counter_templates.py` also owns a closed target-controller payment
leaf over the existing stack-target vocabulary. A fixed ordinary mana vector
or one generic announced cast-X component is resolved through the existing
payment choice, scalar and counter owners. The payer is the target's current
controller, while the countering controller remains the resolving source's
controller. Payment and decline preserve independently typed mandatory siblings;
target invalidation, counter prohibitions and countered-spell destinations
remain owned by the canonical target and stack-counter boundaries. The same
leaf is shared by spell, activated, normalized-trigger, granted and composition
contexts. Nonmana, nonordinary, repeated, chosen or text-defined costs,
fixed-plus-X generic costs, variable activation X, multiple targets and linked
counter-result instructions remain residual. No counter-placement grammar is
expanded by this family.

`compiler/optional_payment_templates.py` owns the historical triggered clause
`you may pay <cost>. If you do, <effect>` when the cost is one positive fixed
ordinary generic, colored, or colorless mana vector and the body is one
independently exact atomic effect. It preserves body-owned targets at trigger
placement and delegates the private resolution decision to the existing
optional-payment handler. Acceptance rechecks affordability and commits the
canonical mana-payment intent before resuming the typed body; decline commits
nothing. Variable, hybrid, Phyrexian, snow, zero, restricted, and nonmana
costs, reflexive `when you do` forms, nested or repeated choices, linked
results, and multi-effect or independently inexact bodies remain residual.

`compiler/power_up_templates.py` specializes the parser's explicit Power-up
marker into a typed price descriptor and once-only activation limit. Both the
Oracle node and runtime catalog use that same specialization. Entry-turn
prices subtract the source's current mana cost through CR 118.7: matching
colored or colorless mana is removed first and excess reduces generic mana.
Hybrid symbols publish the legal reduction-half alternatives; Phyrexian
symbols reduce their color. The ordinary activation proposal revalidates the
chosen price before payment and usage mutation. The shared incarnation usage
journal preserves the limit across turns, control changes and phasing, then
resets it on zone change. Unsupported price symbols and material cost or
result siblings remain residual. Earlier descriptors without the optional
Power-up field keep their serialized meaning and pinned replay provenance.

The selected-object activation cost compiler admits one homogeneous fixed
count from two through ten for discard, owned-graveyard exile, controlled
sacrifice or return. Count and the canonical singular predicate travel together;
unknown plurals, random or linked costs, mixed selected groups and source-plus-
selected costs remain residual. Counted costs use one simultaneous zone batch,
with complete distinct-object validation before mutation. Server-stored
replacement histories resume the entire unpaid cost and retain each selected
object's choice history across pending save/load. One-object payloads retain
their existing execution path. Mandatory counted casting costs remain outside
this activation grammar.

`compiler/fixed_effect_payment_templates.py` extends that same registered
payment choice with an explicit v2 payload across spell, activated and existing
normalized-trigger carriers. One fixed ordinary mana vector, fixed positive
life payment, one or two unqualified owned-card discards, one qualified owned
card discard, or one controlled-permanent sacrifice precedes an independently
closed typed consequence. A single exact mandatory prefix stays outside the
payment scope. The compiler preserves every target and result owner and retains
v1 precedence when its old one-mana/one-effect shape closes. Runtime rechecks
full resources, current predicates and object incarnations, commits through
canonical payment or simultaneous zone intents, then resumes the typed result.
CR 118.12 depends on chosen payment, not the final destination after replacement.
Alternative/compound/dynamic costs, random/named payments, paid-object or linked
results, reflexive triggers, nested/repeated choices, unsupported leaf owners
and wider grammar remain residual.

Fixed kicked-spell additional results use a versioned instruction in the
existing public-condition owner. The recorded stack Kicker fact selects only
the fixed nontargeted result after its independently closed mandatory prefix.
A copy preserves that fact; missing history rejects instead of becoming unpaid.
Invalidated mandatory targets still prevent the whole spell from resolving.
Counter-source attribution remains data owned by the existing counter
transaction. Instead branches and additional conditional targets remain residual.

Public self-death and attached-creature death returns bind the event card and
its exact post-departure zone-change counter. The existing graveyard-return
operation lowers that descriptor to the canonical zone move intent, which
skips missing or changed graveyard incarnations and returns to the owner's
hand. Departure attachment facts retain the dead creature when its Aura later
leaves. Optional, delayed and independently unsupported results remain residual.

`rules/target_announcements.py` publishes a sealed public occurrence for each
newly targeted permanent after an authoritative cast, activation, triggered
ability target choice, stack copy or target change. Duplicate target roles
produce one occurrence; retaining a prior target produces none. The compiler
keeps targeted-object controller and announcing-stack controller distinct,
with source-bound results separate from results for another affected object.
Ordinary Heroic subscriptions still use the spell-cast occurrence, so copying,
activating or changing targets does not become a new cast. Source sacrifice
results share the existing incarnation- and controller-checked owner. Hidden
and player targets, first-time limits and unknown result references remain
residual.

`compiler/kicked_entry_trigger_nodes.py` binds a self-entry `if it was
kicked` ability to the sealed entry occurrence's paid cast option. Casting pays
one fixed kicker through its existing owner; target placement, token creation,
private choices and other consequences retain their independently closed
owners. The original trigger retains the entry fact when its source leaves or
changes controller. A blinked object or token copy has no paid kicker fact and
creates no new kicked trigger. Multiple kicker costs, Multikicker, cost-specific
conditions and independently unsupported results or siblings remain residual.

`compiler/source_maintenance_nodes.py` recognizes self-entry and fixed upkeep
or end-step triggers whose only result is sacrificing the source, optionally
unless the triggering controller pays one existing closed fixed cost. Its v3
payment payload carries the source-incarnation binding and requires a separate
maintenance capability in addition to the payment and zone owners. Controller
steps retain their printed controller condition; an unqualified end step applies
to every active player's turn. Alternative, compound, variable and linked costs,
reflexive results and additional unrepresented text remain residual. Whole-card
admission still requires every independently material sibling to close.

`compiler/monarch_templates.py` owns the mandatory controller-becomes-monarch
instruction. Its strict node shape declares only the existing canonical
designation capability; combat-damage transfer, end-step draw, and
player-leaves behavior retain their separate monarch owners. The same leaf is
available to triggered and activated contexts without trusting the complete
CR 725 mechanic family from prose alone.

`compiler/self_return_templates.py` owns one mandatory nontargeted return of
the source artifact, creature, enchantment, or permanent to its owner's hand.
It emits `$source` and a narrow shape-gated mechanic rather than claiming the
broad zone-change rules family. Targeted returns, source-zone costs, and other
destinations continue through their existing independent owners.

`compiler/fixed_entry_return_requirements.py` composes that source return with
the normalized public entry-event owner and extends the existing APNAP object
choice continuation to one controller-scoped owner-hand destination. It accepts
one through three public permanents matching a closed battlefield
`ObjectQuerySpec`, including the complete alternative of sacrificing the
entering source when the return is declined or cannot be paid. Controlled
fixed-subtype and other fixed-type entries may instead return the exact source
incarnation. Self-return and sacrifice fallbacks use the shared logical-object-
aware source placeholder, so an old trigger cannot move a re-entered permanent.
Untapped basic-land predicates normalize Plains, Island, Swamp, Mountain, and
Forest through an explicit canonical mapping.
All selected objects move simultaneously through the canonical destination-
replacement coordinator. Targets, up-to or aggregate sets, hidden or opponent
subjects, variable or optional returns, compound bodies, and dynamic, chosen,
linked, or unsupported event predicates remain source-spanned residuals.

`compiler/affected_player_sacrifice_templates.py` owns one mandatory fixed
affected-player sacrifice leaf shared by spell, triggered, activated, and
fixed Choose one modal contexts. Target-player, target-opponent, each-player,
and each-opponent relations select one or two public battlefield permanents
through a closed `ObjectQuerySpec`. The existing APNAP selection owner collects
the affected players' choices without early mutation, then a typed simultaneous
move intent enters the canonical zone-destination replacement coordinator.
Optional, variable, all, half, greatest/least, dynamic-characteristic,
combat-state, subtype, color, linked-result, cost, delayed, and rule-generated
sacrifice forms remain source-spanned residuals.

`compiler/affected_player_discard_templates.py` owns the parallel mandatory
fixed affected-player discard leaf for one, two, or three cards. The same
target-player, target-opponent, each-player, and each-opponent relations feed
the existing APNAP selection owner, but hand candidates and prior selections
remain actor-private until one simultaneous replacement-aware move publishes
the actual destinations. Spell, triggered, activated, and fixed Choose one
modal bodies share the descriptor. Random, optional, variable, all-hand,
revealed or qualified, linked-result, cost, cleanup, and keyword-specific
discard forms remain source-spanned residuals. A separate controller relation
permits one through four cards only as a leaf of the closed fixed draw sequence
described above; standalone controller discards remain outside this owner.

`compiler/hand_inspection_templates.py` owns the distinct targeted-hand
inspection grammar. It preserves public reveal versus controller-only look in
one typed descriptor and applies a closed current-characteristic hand predicate.
It keeps positive permanent-type membership conjunctive with explicit Land
exclusion for nonland permanent cards. An unrestricted artifact predicate still
admits a card that is also a land. The shared query owns both constraints.
It routes one mandatory controller-selected discard or exile—or one fixed
all-matching discard—through the ordinary simultaneous replacement-aware zone
owner. The semantic-choice owner exposes the inspected identities only to
authorized principals, revalidates the selected current hand object, and
resumes any independently closed controller life or Scry tail exactly once.
Optional, random, named, chosen, variable, multi-card, cross-zone, cast/play,
delayed-link, linked-quantity, and unsupported carrier forms remain material
residuals.

`compiler/modal_templates.py` preserves the strict fixed `Choose one` spell
owner and separately owns bounded fixed nonrepeating modal blocks across whole
spells, normalized triggers, and supported activated abilities. The expanded
owner accepts `Choose one`, `Choose one or both`, `Choose one or more`, and
`Choose two` only when every bullet already has an exact typed effect owner.
Mode-qualified target-group IDs, printed-order selection, per-mode target
feasibility, runtime target rebasing, and exact child-plus-wrapper capability
closure keep multi-mode execution on the shared target and effect paths.
Repeatable, random, conditional-count, selection-linked-cost, selection-history,
cross-mode-target, unsupported-wrapper, and untyped-branch forms remain
source-spanned residuals. See ADR 0071 for the preserved strict compatibility
shape and ADR 0082 for the expanded owner.

`compiler/cumulative_upkeep_nodes.py` owns the closed printed
cumulative-upkeep grammar. It lowers one fixed positive ordinary-mana cost or
one em-dash-delimited fixed positive life cost to a source-spanned upkeep
trigger. Both forms place the age counter before calculating the optional
payment and require the shared replacement-aware counter owner; unsupported
costs and additional cumulative-upkeep instances remain material residuals.

`compiler/spell_additional_cost_templates.py` owns one closed binary
additional-cost expression. Each side must independently lower to a positive
fixed ordinary-mana payment, a positive fixed-life payment, or one existing
single-object discard, sacrifice, exile, or return payment. The casting owner
publishes one distinct cost-option identity per currently payable branch,
requires the pilot to select that identity when more than one branch is
available, folds a selected mana leaf into the total cost before reductions
and payment mechanics, and commits only the selected nonmana leaf. A spell is
excluded from its own discard candidates because this engine retains the card
in its origin zone until cost commit. Optional, three-or-more-branch,
variable, random, repeatable, composite, reveal, tap, linked-result, and named
mechanic costs remain source-spanned residuals. Direct mandatory positive
fixed-life costs use the same life-payment leaf and canonical life owner.

`compiler/activated_zone_change_costs.py` reuses the spell-cost leaf grammar
for one mandatory selected-object activation cost. It accepts only one fixed
discard, sacrifice, battlefield or graveyard exile, or return payment whose
actor-bound `ObjectQuerySpec` and operation-owned origin and destination are
already closed by the casting-cost owner. Offers and commits obtain candidates
through that same effective-characteristic query. Matching legacy one-object
discard, sacrifice, and return descriptors are promoted to this typed owner;
multi-object, `another`, and mana-ability descriptors remain outside it.
Replacement suspension pins
the selected object and the complete priority-action frame before any mana,
zone, or stack mutation. Source sacrifice joins the existing discard-self and
exile-self transaction through the same continuation identity. Variable,
multiple, optional, random, repeated, linked, dynamic, mana-ability, and
cost-ordering forms remain source-spanned residuals.

`compiler/activation_mana_costs.py` owns fixed complex mana symbols on
otherwise typed nonmana activated abilities. It expands colored hybrid,
two-brid, and Phyrexian choices into immutable mana/life options and retains a
fixed snow-mana count without consulting game state. The source-pinned catalog
serializes those options; offer, proposal, and commit use the same currently
payable option set. `mana_provenance.py` extends the existing mana owner with
compact jointly tagged Snow-source and spending-restriction lots, while
ordinary unrestricted mana retains the historical aggregate pool. X, energy,
half, infinity, chaos, selected-object and other choice costs, open or untyped
nonmana costs, complex-cost mana abilities, reductions combined with complex
symbols, and independently unsupported effects remain source-spanned
residuals.

`compiler/activated_tap_costs.py` owns one separate fixed selected-permanent
tap-cost grammar. It accepts a positive fixed count of untapped permanents the
activator controls when one current effective permanent card type or pinned
subtype completely describes the selection, with an optional source exclusion.
Offers and commits consume the same actor-bound `ObjectQuerySpec`; the payment
owner validates the entire distinct selection before the canonical tap-state
owner mutates any permanent. Ordinary-word tap costs do not inherit the source
tap-symbol summoning-sickness restriction. Source `{T}` and `{Q}` costs, mana
abilities, type or subtype unions, qualified predicates, and Crew, Station,
Convoke, and Improvise semantics retain their existing independent owners or
remain source-spanned residuals.

`compiler/keyword_nodes.py` owns one closed ordinary fixed-mana Morph
production. It lowers the turn-up cost to a typed all-zone runtime component;
the casting proposal owner separately supplies the face-down `{3}` creature-
spell alternative, suppresses printed costs and abilities, and retains
external cost modifiers that apply to the represented face-down spell. The
same descriptor later authorizes a controller-only no-stack turn-face-up
action through the current effective-keyword boundary. Megamorph, variable,
hybrid, Phyrexian, snow, nonmana, copied, granted, text-changed, multiface,
merged, and residual turn-up families remain source-spanned residuals or
fail-closed runtime exclusions.

`compiler/cascade_nodes.py` owns ordinary printed Cascade as one independently
source-spanned stack-zone trigger per instance. The casting transaction
materializes those descriptors into the ordinary APNAP trigger batch and
captures the selected spell face's mana value, including announced X. The
runtime coordinator publicly exiles to the first lower-mana-value nonland,
then delegates the optional cast to the generic one-shot exile-cast choice
owner. That owner revalidates the selected face, no-mana alternative,
additional costs, targets, and spell program before casting; the canonical
zone owner simultaneously returns every uncast card to the library bottom in
a deterministic random order. CR 702.85b action windows, replacement-choice
suspension during the sequential exile loop, and granted, copied,
text-changed, conditional, or face-down Cascade remain fail-closed.

`compiler/storm_nodes.py` owns ordinary printed Storm as one independently
source-spanned stack-zone trigger per instance. The casting transaction reads
only the canonical current-turn spell-cast history before recording the Storm
spell itself, then places Storm and other completed-cast triggers through one
ordinary APNAP batch. The immutable trigger copies represented modes, X,
targets, and the trusted source-program identity. Nontargeted copies are
created without an invented choice; targeted copies use the existing public
Storm target-selection owner, which revalidates each changed target and
commits independent spell-copy objects. Copies are not casts and never add to
a later Storm count. Gravestorm, granted, copied, removed, text-changed,
conditional, face-down, and wider copy-choice forms remain fail-closed.

`compiler/unearth_nodes.py` owns one closed ordinary fixed-mana Unearth
production. It lowers the graveyard-only sorcery-speed activation to a typed
fixed mana descriptor and one `unearth` semantic operation. The descriptor
requires a compiler-pinned complete-card admission certificate because
resolution materializes the card's other behavior on the battlefield. The
runtime coordinator delegates return, Haste, leave replacement, and delayed
trigger behavior to their existing typed owners. Variable and nonmana costs,
copied or granted instances, multiface cards, and cards with other material
residuals remain source-spanned residuals or fail-closed runtime exclusions.

`compiler/flashback_nodes.py` owns ordinary fixed-mana and fixed-mana-plus-
fixed-life Flashback productions. It requires complete current CardProgram
admission before granting
the owner a graveyard cast offer, contributes only the server-authored
Flashback alternative unless another typed permission independently authorizes
the printed cost, and otherwise reuses ordinary timing, targets, additional
costs, mana payment, stack resolution, and countering. Cast commit records one
incarnation-local designation; the zone-replacement owner applies its mandatory
stack-leave self-replacement before competing destination replacements and
clears the designation in the new zone. The unlock frontier normalizes literal
fixed cost parameters into one reusable Flashback grammar family rather than
one candidate per literal. Variable and wider nonmana costs, Flashback-specific
cost modifiers, partial cards, copies, grants, text changes, and unsupported
graveyard permissions remain source-spanned residuals or fail closed.

`compiler/kicker_nodes.py` owns one single fixed ordinary-mana Kicker cost and
one closed kicked counter-plus-keyword entry replacement. The cost component
adds a server-authored optional total-cost branch only for a complete admitted
CardProgram. Its paid stack fact flows into immutable zone-replacement and
normalized entry snapshots. The entry component creates a nested replacement-
aware +1/+1 counter event and optionally grants Flying, First Strike, Haste, or
Trample through existing consumers. Multiple, and/or, variable, nonmana,
copied, granted, kicked-trigger, spell-rider, dynamic, and open entry families
remain source-spanned residuals.

`compiler/activated_mana_nodes.py` also owns four exact source-self zone-move
effects: graveyard to owner hand, graveyard to battlefield tapped or untapped, and a
battlefield Aura to owner hand. The typed descriptor replaces the parser's
default battlefield active zone with the represented origin and records the
destination, tapped state, source form, and complete-card policy. Only the
battlefield result requires the shared complete-card admission certificate;
the hand-return forms may remain independently executable on partial cards.
Qualified source-self, targeted, mass, optional, conditional, multiple-object,
copied, granted, and text-changed movement stays source-spanned and residual.

`compiler/reanimation_templates.py` owns the complementary fixed target
reanimation grammar across spell, triggered, activated, modal, and Saga bodies.
It accepts one mandatory or up-to-one permanent card from a public graveyard,
closed permanent type, subtype, supertype, union, fixed mana-value, and exact
represented fixed-power predicates, the resolving controller or card owner,
and tapped or untapped entry. The emitted `reanimate` operation reuses the
shared resolution-time target revalidation, typed Aura-entry, destination-
replacement, and canonical zone-transition owners. Numeric graveyard targets
use the same exact current-characteristic boundary as battlefield targeting;
unrepresentable characteristic-defining or cyclic values fail closed. Mass,
untargeted non-source, delayed, linked-result, entry-rider, variable, copied,
granted, hidden-origin, and compound forms remain material residuals.

`compiler/public_zone_move_templates.py` owns fixed public-origin movement as
one shared grammar across spell, triggered, activated, and modal bodies. It
lowers a target physical card in any graveyard through a closed card-type,
card-type-union, or excluded-type predicate, fixed graveyard-owner sweeps,
and closed battlefield affected sets moving toward exile or each object's
owner's hand. The same descriptor admits closed graveyard sets moving to owner
hand or entering under the owner or resolving actor, with an explicit tapped
policy; prospective controller facts and the final replacement-adjusted group
flow through the canonical simultaneous zone transaction. Set membership uses
the same cycle-safe `ObjectQuerySpec` boundary as other affected-set owners and
is frozen in APNAP order. Closed creature-subtype exception lists are typed
query data rather than runtime prose. Variable, optional, linked-result,
delayed-return, open exception, chosen-quality, dynamic-count, numeric-
characteristic, attachment-dependent entry, and multiple-destination forms
remain source-spanned residuals. Commander-profile trust also requires the
typed CR 903.9 owner choice; the compiler never excludes commanders from an
otherwise universal Oracle instruction.

`compiler/hand_entry_templates.py` and
`compiler/public_tap_state_set_templates.py` reuse that public-object query
boundary for two adjacent fixed families. The former offers one actor-private,
owner-pinned hand choice over represented land, creature, Equipment, artifact,
and historic-permanent predicates. Historic entry uses a closed union of the
existing object queries: artifact, legendary permanent, or Saga. Legendary
nonpermanents do not qualify. A fixed draw may precede the optional choice;
the committed draw runs once before current hand candidates are discovered.
Selection seals the card's logical incarnation and revalidates its ownership
and predicate before the ordinary replacement-aware battlefield move and
after any entry suspension. A qualifying Aura uses the existing nontargeted
attachment choice; Shroud does not prohibit that choice, while Protection and
Enchant legality still apply. With no legal recipient, the Aura stays in hand.
The latter locks one current
public permanent set and delegates each fixed tap or untap result to the
canonical tap-state owner, including stun-counter replacement. Choice options
are ordered by stable object reference so checkpoint replay cannot depend on
in-memory card insertion order. Direct generic Aura selection, unrepresented
attachment domains, additional entry counters, attacking entry, linked later
references, wider hidden selection, continuous untap prohibitions, and
dynamic or chosen tap predicates remain residual.

`compiler/fixed_owner_zone_move_templates.py` owns the complementary closed
single-object and public-choice grammar whose destination is determined by the
moved object's owner. Targeted battlefield, graveyard, and face-up exile
objects, the source's current incarnation, a current-or-last-known enchanted
creature, one controller-selected tapped land, and one creature selected by
each player lower to the existing target, attachment-reference, APNAP choice,
and zone-transition owners. Library moves admit only top, bottom, second,
third, or fourth position, plus the exact enchanted-creature shuffle form.
Heterogeneous spell-or-permanent targets, hidden selection, broader mass
choices, controller destinations, alternative costs, delayed or linked moves,
and arbitrary positions remain source-spanned residuals.

`compiler/library_search_templates.py` owns fixed restrictive searches of the
controller's library across spell, triggered, activated, and modal bodies. The
compiler emits one typed hidden-zone selector that uses the shared cycle-safe
`ObjectQuerySpec` effective-characteristic boundary. The searching seat receives
an actor-private candidate set and may fail to find a stated quality under CR
701.23b. One selected type-, subtype-, supertype-, or color-qualified card may
move to hand through the existing replacement-aware zone owner, with only an
explicitly printed reveal becoming public under CR 701.23e. A single supported
permanent may instead enter the battlefield through ordinary replacement-aware
entry. Bounded multi-card battlefield support remains limited to land cards
that all enter tapped, so the canonical zone owner can commit one simultaneous
entry batch before the deterministic shuffle. Canonical `it`, `them`, `that
card`, and `those cards` references preserve the same typed result identity.
Unrestricted, named, dynamic, chosen, cross-field, multiple-card hand,
different-name, attachment, linked-result, compound-tail, other-player,
search-limiting replacement, and search- or shuffle-trigger forms remain
source-spanned residuals or outside this capability's trust boundary.

The same activated-effect owner admits closed fixed characteristic results
through the existing resolution-created continuous-effect capability. It
lowers fixed numeric self power/toughness changes, fixed numeric
controller-creature affected sets, and the closed self keyword vocabulary to
the canonical duration journal and effective-characteristic query. The shared
affected-set shape accepts only compiler-canonical battlefield queries over a
fixed controller relation and closed type, subtype, color, or supertype
qualifier. Dynamic or state-derived quantities, state predicates, token
predicates, unsupported keywords, missing or alternate durations, copy and
face-down semantics, and player or game-rule effects remain residual. This
producer adds no family-specific ability-presence check and performs no dynamic
characteristic count.

`compiler/fixed_control_templates.py` admits one direct public permanent or
one closed public battlefield set with fixed control duration. Direct source
durations require the original permanent context and lower only the source
remaining present, controlled, tapped, or the supported conjunctions. Set
instructions retain printed untap/control/haste order and lock their complete
original incarnation set before any substep. The typed control handlers use
`continuous.control.fixed_resolution`; `control_effects.py` commits through
the existing layer-two journal and custody history, without reading Oracle
text during play. Exchange, dynamic subjects, unsupported durations, and
independently unsupported siblings remain residuals.

The source-only optional untap clause lowers to a separate component in the
existing static untap participation family. `untap.step.optional_source`
authorizes the active controller's retained zero-or-more choice. The planner
and physical untap coordinator own participation and simultaneous untapping;
optional additional untaps on other players' turns, wider optional queries,
selection limits, and phasing execution remain outside this contract.

`compiler/public_state_queries.py` owns the shared fixed battlefield-query and
public-state condition grammar consumed by continuous characteristics, typed
queried ability grants, resolution-locked characteristic sets, and untap-step
participation. It accepts only battlefield sets representable by one immutable
`ObjectQuerySpec` over a source-controller, source-opponent, or global relation.
Closed predicates cover card-type unions, pinned creature-subtype conjunctions
and unions, the defined Outlaw group, color and multicolor cardinality,
supertype, token identity, a named +1/+1 counter, attacking, blocking, tapped,
untapped, enchanted, equipped, modified state, and positive supported-ability
presence. Fixed layer-6 and layer-7c components reuse that same query, including
combined wording. Ability-presence applicability gains generic dependencies on
same-layer additions or removals of the required ability; unrelated abilities
do not create false dependencies, and cycles retain the ordinary deterministic
fallback. Resolution-locked queries stop at the cycle-safe layer-5 boundary and
therefore reject ability-presence predicates. Every emitted keyword node still
declares each existing combat, damage, destruction, or targeting consumer.
Singular attachment-relative, negative or absent ability, compound state-and-
counter, dynamically counted, open conditional, chosen, hidden-zone, and
unsupported-keyword forms remain source-spanned residuals. Matching Class lines
also remain residual until level applicability has a typed owner.

Printed and fixed layer-6 Protection productions share `ProtectionSpec` and
the existing `protection.typed.debt` capability. The closed v2 source predicate
normalizes fixed type, pinned subtype, negative creature-subtype, supertype,
color-cardinality, and minimum-mana-value qualities, including separate
`and from` qualities and ordinary comma/semicolon keyword siblings. The one
runtime verdict reads current effective characteristics for targeting,
blocking, and attachments and last-known `DamageSourceSnapshot` facts for
damage. Cast-history, counter-state, modified, chosen, and otherwise open
qualities remain source-spanned residuals. This grammar adds no separate
layer-6 ability-presence query and no dynamic characteristic count.

`compiler/devoid_characteristics.py` owns the ordinary printed Devoid
production. One
exact keyword instance lowers to an all-zone
`ability.static.colorless-characteristic-definition.v1` fragment. Copy values
carry that fragment at layer 1; the characteristic evaluator then removes all
colors as a characteristic-defining effect in layer 5 before later non-CDA
color effects. Commander color identity remains a separate database-derived
format characteristic. Nonordinary wording, untyped granted Devoid,
text-changing producers, and face-down producers remain residual or outside
trust; the production performs no dynamic characteristic count.

`compiler/changeling_characteristics.py` and the shared characteristic-
definition node owner likewise lower one ordinary printed Changeling instance
to `ability.static.all-creature-types-characteristic-definition.v1`. The
copied fragment applies every subtype from the pinned CR 205.3m vocabulary as
a layer-4 CDA in every zone. Copies retain the fragment, the CDA precedes later
non-CDA type setting, and layer-6 ability removal cannot undo the earlier type
result. Bare keyword text, nonordinary wording, untyped grants, text changes,
and unrepresented face-down or copy values remain outside trust. The source-
local definition performs no state-dependent count; subtype consumers read the
ordinary effective type line instead of a Changeling-specific branch.

`compiler/bestow_nodes.py` owns one ordinary fixed-mana Bestow production. It
emits a complete-card-required all-zone descriptor rather than a cost-only
spell program. The casting owner turns that descriptor into a server-authored
alternate cost with Aura cast characteristics, one public creature target, and
the existing `bestow_prepare` resolution effect. This preserves the ordinary
permanent destination, resolves after target loss as a creature, and delegates
attachment and attached modifiers to their existing owners. Variable and
nonordinary costs, partial cards, unsupported attached results, copies, grants,
text changes, multiface cards, tokens, phasing-in unattached, and wider cast
permissions or prohibitions that distinguish creature from Aura spells remain
source-spanned residuals or explicit trust exclusions.

The shared Aura grammar accepts qualified battlefield-object restrictions
through `SimpleEnchantSpec` and a second closed `TypedEnchantSpec`, both of
which lower to the same target-query and attachment owners. The typed form
represents players and opponents, public creature or instant cards in a
graveyard, effective type and subtype alternatives, supertypes, colors,
commander status, and controller relations. Cross-axis alternatives such as
creature or Vehicle use `TargetCharacteristicForm`; they do not introduce an
Aura-specific characteristic evaluator. Cast offers, resolution revalidation,
nonspell entry, and state-based attachment legality all consume the current
effective source-pinned characteristic snapshot. Player Auras use the same
reciprocal attachment owner with an internal typed player identity and project
only the public seat.

`compiler/fixed_attachment_templates.py`, `rules/attachment_actions.py`, and
`compiler/fixed_attachment_keyword_nodes.py` own a separate closed attachment-
action family. It lowers one source Equipment entry attachment, one source Aura
reattachment, fixed-mana Equip restricted to a creature token, commander,
legendary creature, or one creature subtype, and the ordinary Living Weapon and
For Mirrodin! entry triggers. Resolution revalidates the source incarnation and
current target before delegating to the existing reciprocal attachment owner;
the keyword triggers first use the replacement-aware token owner and attach the
source only to the original Germ or Rebel. Additional replacement-created
tokens remain unattached. Multiple targets, other attachment source kinds,
broader Equip costs or restrictions, modified keyword token definitions, and
replacements that multiply or alter the original token remain source-spanned
residuals.

A syntactically complete but unsupported Enchant line produces one precise
restriction residual instead of a second generic mechanic blocker. Numeric
power, toughness, and mana-value predicates, dynamic characteristic counts,
modified or attachment-qualified objects, keyword-negative predicates,
nonbasic predicates, open-ended variants, text-changing or untrusted face-down
characteristics, and multiple Enchant abilities remain source-spanned
residuals.

`compiler/target_effect_corpus_assurance.py` independently reconstructs the
resolution body for every promoted standalone or sequenced fixed-target node,
then requires the source grammar, emitted effects, target relation, closed
capability shape, and declared capability closure to agree. The normal pinned
compiler census derives grammatical shapes and representative identities from
the complete corpus; it does not maintain a card list. Its synthetic contract
also covers every accepted keyword, spell/trigger/activation context,
two-/three-clause and target-/counter-first sequence, controller relation, and
closed characteristic predicate and source-exclusion dimension. Adjacent
optional, modal, variable, compound-result, repeated, and multi-target forms
must remain residual. The generated assurance lives in the Oracle coverage
reports and contains hashes and public identities rather than Oracle prose.

`compiler/counter_placement_templates.py` separately owns the closed
fixed counter-placement grammar. Source-self recipients use the existing
`$source.zone_object` resolver so a queued result cannot modify a returned new
incarnation. The separate source provenance reference keeps its existing
meaning, and unavailable source instructions leave independent results intact.
`counter_doubling_templates.py` admits one fixed named-counter kind over those
same source, attachment, direct-target and affected-set subjects. It emits a
closed amount descriptor and the dedicated named-doubling capability. The
runtime reads current counts at each instruction and uses the ordinary placement
owner; no new operation or targeting grammar is introduced. Unknown, all-kind,
chosen, player and independently unsupported sibling forms remain residual.
Direct targets lower once to
`DirectPermanentTargetSpec`, whose deterministic runtime schema supports the
represented type conjunctions and canonical disjunctions of up to four
permanent card types; pinned positive and negative creature subtypes; bounded
positive and negative supertype, keyword, color, color-cardinality, and token
qualities; controller relation; source exclusion; one fixed
exact/minimum/maximum mana-value qualifier; and one shared typed public-state
predicate for tapped state, named-counter presence, or current-turn battlefield
entry. That public-state descriptor also represents one current attacking,
blocking, or attacking-or-blocking creature predicate from the authoritative
combat relationships. The final effective-characteristic snapshot owns color cardinality and
negative subtype evaluation at offer, command, and resolution boundaries, so
copy and type-changing effects do not require a second grammar-specific query.
The mana-value form
uses the existing current public characteristic snapshot and remains separate
from power, toughness, variable, total, and public-state-combined numeric
grammar so this harvest does not cross the cycle-sensitive characteristic
boundary.
Arbitrary adjectives are never inferred as creature subtypes. Scoped
disjunctions, combat or damage history beyond current participation, power or toughness, generic counter
presence, and identity or attachment relations remain residual. Ability-
presence predicates remain residual until the shared layer-6 applicability
query exists. Targeted
destruction, exile, damage, return, fixed target-characteristic changes, tap,
and untap delegate their whole-clause subjects to this
same owner, so spells, triggers, activations, and modal bodies share the exact
typed target grammar without an effect-specific predicate vocabulary. Mixed
type/subtype disjunctions and unrepresented qualifiers remain residual. The
counter owner preserves two or
three printed fixed placements on one shared source or direct permanent target
as one typed batch node; runtime code receives the typed node and never
reparses Oracle text.

The same compiler owner lowers optional bounded permanent target sets from
both “up to N target” and “each of up to N target” wording. It emits one
zero-to-N target schema and one typed simultaneous placement instruction;
spell, triggered, and activated contexts share that production. The closed
tapped-creature form uses the same public-state predicate and resolution
revalidation as a single direct target. Variable limits, subtype or other
combat-state predicates, and compound instructions remain
source-spanned residuals.

Before event binding, the Oracle IR carrier boundary removes a leading ability-
word label only when the remaining material begins with `When`, `Whenever`, or
`At the beginning of`. The node retains the complete original source text and
span. An ordinary Roman-numeral chapter marker lowers only on a printed Saga
with one contiguous face-level chapter inventory and an independently exact
typed body. One source line shared by multiple chapter numbers receives a
stable program identity per threshold; the resulting `saga.chapter.N` events
remain in the existing replacement-aware lore progression, APNAP trigger, Read
Ahead, final-chapter, replay, and state-based-action owners. Malformed,
non-Saga, copied, granted, conditional, linked, dynamic, or independently
inexact chapter material remains residual. Each ordinary chapter program also
contributes its semantic identity to the shared current layer-6 ability
snapshot. Threshold dispatch and final-chapter cleanup therefore suppress a
removed or absent current-face chapter without stopping lore progression or
replaying a threshold after the ability returns. Other nontrigger ability words,
quoted text, and unsupported event or effect grammar also remain material and
fail closed through their existing owners.

`compiler/fixed_counter_trigger_nodes.py` owns one shared closed normalized-
event binding for represented beginnings of steps; a land entering under the
source controller's control; a noncreature or instant-or-sorcery spell cast by
the source controller; one closed static color, color-cardinality, type,
subtype, or supertype spell predicate with at most two alternatives and a
source-controller, opponent, or any-player caster relation; the source creature attacking;
controller life gains, card draws, and exact second draws; and public artifact,
creature, enchantment, or permanent entries plus creature deaths. Cast
predicates consume only the immutable controller, cast-selected types,
subtypes, and supertypes, and effective colors already sealed by the normalized
cast event. The cast transaction takes type-line fields from the validated cast
proposal so selected faces and cast methods remain authoritative, and takes
colors from the cycle-safe effective stack object so all-zone definitions such
as Devoid apply before the snapshot. The self-attack predicate consumes the
exact attacker reference derived from the canonical completed attack
transition. A mandatory body may compose with those bindings only when the body
already lowers through one reviewed typed effect owner with a trusted
capability closure. Counter-producing bodies retain their counter-specific
capability and optional-choice wrapper rather than entering a second trigger
path.

The public zone-change grammar lowers only closed controller, opponent,
source-exclusion, token, and single-subtype predicates. It consumes the
normalized owner's current entry facts or predeparture last-known facts and
does not perform a characteristic query of its own. The binding emits only an
immutable event predicate and ordinary triggered node; APNAP placement, target
selection and revalidation, replacement suspension, and effect mutation stay
with their existing owners. Subtype “dies” clauses consume `creature.dies`,
while subtype “is put into a graveyard from the battlefield” clauses consume
`permanent.graveyard`; the latter therefore includes noncreature Kindred
permanents with that creature subtype. Cast-or-copy, cast history and origin,
payment and kicked state, targeted-spell relations, mana-value thresholds, dynamic
characteristic counts, characteristic conjunctions, broader land-entry relations, attack-recipient or
aggregate attack forms, characteristic-qualified zone changes, one-or-more
aggregation, combined events, intervening-if, reflexive, optional noncounter,
variable, linked, and unrepresented bodies remain material. Same-layer and
dynamic stack-characteristic interactions outside the sealed cast snapshot
remain explicit trust exclusions.

`compiler/counter_keyword_activation_nodes.py` composes that counter owner
with one source-pinned activation family for fixed ordinary-mana Level Up,
Outlast, Reinforce, and Scavenge. Level Up and Outlast resolve the exact current
source zone object; Reinforce and Scavenge use one revalidated creature target
and the shared replacement-aware source-zone cost transaction. Reinforce uses
ordinary priority timing; Level Up, Outlast, and Scavenge use sorcery timing.
Action offers and submitted commands consume that same compiler-pinned timing
field rather than applying mechanic-specific checks at either call site.
Scavenge lowers only a positive integral power printed on a single-face card.
Star power,
characteristic-defining or otherwise dynamic counts, and copy, face, text, or
type-changing interactions remain residual until a cycle-safe zone-
characteristic boundary owns them.

`compiler/leveler_context_nodes.py` recognizes only ordinary creature Leveler
layouts with one finite `LEVEL N-M` striation followed by its contiguous
`LEVEL N+` striation and fixed nonnegative power/toughness in both. It combines
each header with its printed power/toughness span, records independently exact
child-program keys in one typed descriptor, and gives those children the
shared current-ability-fragment coverage marker. A typed component-scope
fragment binds those children to the parent level symbol and removes card
data's flattened band-keyword inventory from base values. The runtime
continuous owner uses the committed public level-counter count to remove
inactive child components and rematerialize only the active keywords at the
source timestamp in layer 6, then set the active base power/toughness in layer
7b.
Level Up itself remains outside the bands and available at every level through
its existing activation owner. Class, Room, Prototype, noncreature or arbitrary
striations, malformed ranges, variable characteristics, unsupported children,
granted or text-changed symbols, and cycle-sensitive dynamic characteristic
interactions remain residual or explicitly outside trust.

`compiler/class_context_nodes.py` recognizes the ordinary Class layout only
when the exact reminder is followed by one fixed ordinary-mana Level 2 bar and
one fixed ordinary-mana Level 3 bar in order. Each bar lowers into an ordinary
sorcery-speed stack activation plus a distinct static scope. The activation
catalog and proposal/commit owner enforce the current sequential level and
payment; resolution asks the permanent-designation owner to advance only the
pinned source incarnation. Independently exact children receive the existing
current-component marker, and both level scopes use the shared layer-6
applicability query so level 3 adds rather than replaces level 2 abilities.
The same context admits the exact source-self "becomes level N" occurrence
through normalized trigger placement, and the ordinary static compiler exposes
the reusable no-maximum-hand-size component used by cleanup offer and commit
validation.
Variable, hybrid, Phyrexian, snow, nonmana, malformed, granted, text-changed,
direct level-setting, independently unsupported, and cycle-sensitive dynamic
characteristic forms remain residual.

`compiler/day_night_nodes.py` and
`compiler/spell_history_transform_nodes.py` close one paired-face family.
Canonical Daybound and Nightbound keywords must occupy opposite faces of a
nonmodal double-faced card. The legacy grammar accepts only the two exact
"beginning of each upkeep" previous-turn spell-count conditions and a bounded
source-self reference. Both forms use the shared current-ability component
query, one bounded previous-turn summary, and the logical-object-aware
transform operation. Other steps or thresholds, arbitrary Transform or
Convert instructions, explicit effects that set day or night, transform-into
effects, copied or granted forms, and independently unsupported siblings
remain material residuals.

`compiler/token_templates.py` and
`compiler/fixed_token_production_templates.py` own fixed-definition token creation across
spell, triggered, and activated effects. The closed production emits a
positive fixed quantity, an optional tapped entry state, and either a
represented predefined artifact definition or a fixed creature definition
with up to three colors, optional Artifact and Enchantment card types, a
proper name or Legendary supertype, and capability-backed keywords. Changeling
and the exact can't-block or can't-be-blocked token sentences lower to the
shared typed characteristic and declaration fragments rather than executable
display text. The same owner lowers canonical fixed Investigate, fixed
Afterlife through permanent-graveyard LKI, one current-target copy token, and
one source-independent next-end-step creation. `compiler/token_copy_templates.py`
adds fixed copy recipes referencing the source, one qualified public permanent
target, or a represented zone-event object. Copiable fixed exceptions retain
nonlegendary, base-stat, color, subtype, card-type and keyword distinctions.
Quoted granted abilities use the existing typed-token child programs; recurring
copied cleanup is not converted to an independent delayed trigger. Normal
Populate uses the existing token-copy choice owner without the historical
Haste-and-sacrifice rider. Every form reaches the existing replacement-aware
`token_creation.py` transaction. Incubate, Roles, attached or attacking tokens,
linked, chosen, hidden, graveyard or spell copy origins, face-down copy values,
arbitrary quoted abilities, unsupported keywords, other delayed times, and
independently unsupported compound or conditional instructions remain
source-spanned residuals or explicit unsupported-interaction boundaries.

## Invariants

- Every lowered node retains its exact source provenance.
- Unknown or ambiguous grammar becomes a source-spanned residual, never
  guessed behavior.
- Instruction order, targets, modes, choices, and continuations remain
  explicit in the typed result.
- Parsing success is separate from runtime and rules closure.
- A reviewed ability can supersede generated output only at the same stable
  semantic key. The one bounded inverse case is a trusted typed multi-event
  program replacing a complete, body-identical reviewed split of those same
  source-self subscriptions; incomplete or ambiguous splits fail closed.
- Compiler output cannot claim trust beyond all declared capabilities and
  runtime dependencies.
- Card names and Oracle IDs are evidence and lookup keys, not generic runtime
  behavior switches.

Activated-mana lowering has separate closed owners for fixed output and
current color-set output. The color-set grammar represents choosing one color
among qualifying legendary permanents or legendary creatures and
planeswalkers, choosing among owned legendary creature cards in a graveyard,
and adding one mana of each color among controlled permanents. Each form
lowers an immutable relative `ObjectQuerySpec`. Monocolored-only, linked-exile,
opponent-relative, additional-condition, and side-effecting variants remain
source-spanned residuals. The fixed-output owner may also retain one typed
once-each-turn activation limit, one closed controller basic-land-subtype
query, or one spending predicate over current public spell or ability-source
types, subtypes, and supertypes. Offer and commit both consume the same
activation query and object-incarnation usage journal, while casting and
activation payment share the same canonical mana-spend context. Origin,
casting-method, color, mana-value, variable, compound-condition, result-rider,
and delayed-effect restrictions remain source-spanned residuals.
The parser must consume the complete printed spending-restriction suffix before
the fixed-output compiler removes it from the mana instruction. Exact historic
artifact-, creature-, legendary-, and nonartifact-spell wordings retain their
legacy serialized identities; broader representable spell/ability unions use
the typed predicate encoding. A recognized prefix cannot discard an origin,
chosen-quality, casting-method, or result-rider suffix.

Printed `Affinity for` qualities in the closed casting-payment vocabulary lower
as source-spanned `cast.cost` descriptors containing canonical effective-object
queries. The runtime evaluates their union once per controlled phased-in
permanent, so an object matching several historic-permanent branches still
reduces the generic total only once. Unsupported qualities, granted or removed
Affinity, and equivalent rules text remain residual instead of becoming runtime
Oracle interpretation.

The same selected-face cast-cost registry separately owns fixed and public-
value modifiers. One immutable descriptor can read a closed spell predicate,
public object count or threshold, current source counter, party size, permanent
color cardinality, effective-name count, total mana value, devotion, Domain,
life difference, or the canonical discard, sacrifice, life, attack, spell, and
Commander-cast facts. The result is a fixed generic or colored vector applied
in the ordinary total-cost stage. Battlefield predicates use current effective
type, subtype, color, keyword, name, and named-counter state; graveyard and
exile predicates remain actor-relative public queries. Target-relative prices,
power/toughness amounts, distinct type or mana-value sets, chosen or secret
qualities, caps, floors, minimum totals, direct total setters, substitutions,
and open arithmetic remain source-spanned residuals.

Party size is the maximum deterministic assignment of distinct controlled
creatures to the Cleric, Rogue, Warrior, and Wizard roles. One multitype
creature can fill only one role, and battlefield iteration order cannot change
the result.

Ordinary printed `Improvise` and `Delve` lower through the same selected-face
casting-payment component family. Improvise advertises current controlled
untapped artifacts and commits their tap costs through the casting transaction;
Delve advertises owner-graveyard cards and commits immutable, revalidated
graveyard-to-exile identities. Both pay only generic mana after the represented
total cost is determined. Delve composes with Convoke in one payment proposal;
parallel tap-payment families remain fail closed.

Fixed ordinary or colored-hybrid `Evoke` costs lower to typed alternative-cost
branches carrying one immutable payment marker. Permanent-spell resolution
turns that marker into an ordinary normalized entry-trigger occurrence, so its
sacrifice uses the shared APNAP trigger batch and canonical sacrifice effect.
Nonmana Evoke remains unsupported except for an exact reviewed typed override;
variable, Phyrexian, snow, granted, removed, and equivalent-text forms remain
outside this compiler family.

Fixed ordinary-mana Buyback, Dash, Escape, Foretell, Plot, Warp, and
positive-count Suspend, plus bare Jump-start, Rebound, and Retrace, lower
through one typed cast-lifecycle owner. Every descriptor declares both public
and zone-cast capabilities required by the registered shared handler, plus its
kind-specific cost or combat dependencies. This conservative descriptor
contract does not grant a new casting permission or bypass current-ability
validation; unavailable dependencies keep admission fail-closed. Dash contributes
an alternative cost whenever the canonical casting owner already authorizes the
card's current zone, including a designated commander in the command zone; the
ordinary total-cost owner adds commander tax. Warp remains hand-only. Suspend
contributes a timing-gated hand special action rather than a cast: it pays only
the Suspend cost, moves the card face up to exile, and places time counters
through the existing zone and counter owners. Its owner-upkeep and last-counter
triggers use ordinary APNAP placement; the latter offers a current cast without
paying the mana cost through the existing one-shot exile-cast and casting
owners. A creature cast this way receives an identity-pinned Haste effect that
expires permanently on the first control change.

Foretell and Plot add no-stack hand actions through the same priority and mana
transaction. Foretell creates an owner-private, distinguishable face-down exile
designation and a later-turn fixed alternative cost. Plot creates a public
designation and a later-turn sorcery-window cast without paying the mana cost.
Those rules-created designations survive loss of the printed ability but end on
a zone change. Escape and Jump-start instead require their current graveyard
ability: Escape combines a fixed ordinary alternative cost with exactly N
distinct other owned graveyard cards through the fixed-set zone-cost owner;
Jump-start combines the printed cost with one typed discard and a mandatory
stack-leave self-exile replacement. Rebound applies only to a successful
hand-cast instant or sorcery resolution, then schedules one identity-pinned
next-upkeep optional cast through the existing one-shot exile owner. A
countered initial spell and an exile card that left and returned do not
Rebound.

Fixed-output mana nodes also declare the unchanged shared handler's complete
capability contract. This does not restrict otherwise unrestricted mana or add
payment authority; the existing typed spending predicate remains authoritative.

The separate fixed public alternative-cost owner lowers complete-card-admitted
plain “rather than pay” declarations and fixed ordinary-mana Freerunning,
Prowl, Spectacle, and Surge. Plain declarations may contain one fixed mana or
life payment, one typed discard, sacrifice, hand-exile, or battlefield return,
or the reviewed mana-plus-single-zone-change forms. Eligibility reads only the
canonical active turn, controlled basic-land characteristics, spell-cast
history, life-loss history, or combat-damage source snapshot. The resulting
option enters the existing cast proposal, total-cost, atomic payment,
commander-tax, current-ability, and replay owners. Trap, private or arbitrary
conditions, team-specific history, tap/reveal/opponent/library/counter/random
payments, variable or complex mana, multiple nonmana payments, and sibling
additional, lifecycle, or alternative costs remain residual.

Lifecycle lookup uses the shared static-component applicability query before
offers, commits, and the represented Suspend upkeep trigger. Because the layer
engine does not yet evaluate exact ability additions or removals outside the
battlefield, any relevant off-battlefield ability-layer effect makes the
lifecycle unavailable rather than assuming its printed ability remains. That
fail-closed boundary applies to current-ability Escape, Jump-start, Retrace,
and Suspend, but not to an already-created Foretell or Plot designation.
Variable, modified, duplicate, copied, granted, incomplete-card, land,
external foretell/plot, Escape follow-on, external exile-counter, and other
distinct lifecycle forms remain outside this boundary.

Ordinary fixed-mana `Madness` lowers as two source-spanned abilities. Its
static hand component replaces only a typed discard's requested graveyard
destination with exile; ordinary hand moves cannot acquire discard authority
from their zones. The resulting normalized self-discard event creates an
identity-pinned cast-or-graveyard trigger. Its public choice derives one scoped
alternative cost from the ordinary casting proposal and commit owner, so cost
modification, target legality, payment rollback, stack construction, and
countering retain their canonical paths. Activation and spell costs, effects,
cleanup, specifically drawn-card actions, and simultaneous APNAP choices all
share the same typed discard cause. Variable, hybrid, Phyrexian, snow, life,
nonmana, compound, altered-reminder, copied, granted, and incomplete-card
Madness forms remain fail closed.

Printed `Sunburst` lowers as one source-spanned `zone.change` descriptor per
instance. The descriptor contains the counter kind derived from the printed
selected-face card types, never a runtime type query. Cast commit separately
records the distinct WUBRG colors actually spent; only a resolving cast card
with a nonempty payment fact can apply the descriptor. Parameterized or
qualified wording, Modular—Sunburst linked values, nonkeyword equivalents,
and ability propagation outside the typed fragment remain material residuals.

The generic self-entry counter compiler separately accepts one mandatory
source-self sentence whose amount is cast X, distinct mana colors, a bounded
current-turn attack, spell, death, or opponent-life-loss fact, hand or mana
payment provenance, or a closed public object count. Query amounts use the
shared cycle-safe layer-5 `CharacteristicQuantitySpec`; the zone replacement
snapshot freezes every resolved amount by component before CR 616 ordering.
Permanent spell copies retain copied X while every cast-only fact remains
absent, and the shared current-component marker makes layer-6 ability removal
fail closed without a self-entry-specific ability query.
Choice, repeated-kicker, unavailable-history, later-layer dynamic, external-
entrant, and compound forms remain source-spanned residuals.

Closed controller-wide static permissions lower to selected-face
`action.permission` descriptors. The grammar includes playing lands from the
controller's own graveyard, activating abilities of controlled creatures as
though they had haste, private or public current-library-top visibility,
fixed public land or spell predicates for the controller's current library
top, and one or two additional land plays on each controller turn. Library
spell predicates always exclude lands, while historic, Snow, type, subtype,
colorless, and bounded public keyword forms use the shared object-query model.
Runtime action, activation, projection, and land-quota queries consume only
those trusted typed descriptors and the shared current layer-6 component
boundary. Chosen or dynamic predicates, linked costs or results, non-top or
opponent-library access, conditional or temporary permissions, targeted
forms, wider land-play counts, and ordinary haste wording remain
source-spanned residual material.

Static cast-timing descriptors preserve the exact “Aura spells with enchant
creature” qualification as the existing typed Enchant spec. Offer and commit
compare that spec with the candidate Aura's compiler-pinned Enchant fragment;
the Aura subtype or its currently available target does not substitute for the
ability restriction. Unrestricted Aura timing remains a separate query, while
dynamic, multiple, compound, or unsupported Enchant qualifications fail
closed.

Printed Exhaust prefixes lower to a typed `ActivationLimit` on each distinct
ability. The exact reminder sentence is stripped once by the ability parser;
neither legality nor commit reparses it. Fixed-output and color-set mana
descriptors carry that limit, and each nonmana result still needs its own
ordinary effect and cost closure. Wording that permits another Exhaust use
remains a material residual.

Exact fixed regeneration instructions lower to one `regenerate` operation over
`$source.zone_object`, a revalidated direct artifact, creature, or permanent
target, or the typed current-or-last-known creature attached to the source.
Exact direct-target and fixed-set destruction may carry an immediately
adjacent cannot-be-regenerated rider as a flag on the same destruction effect.
Cost, event, target-predicate, and sibling-effect compilation remain
independent, so unsupported surrounding grammar stays residual even when the
fixed effect sentence matches. Static, variable, repeated, optional,
conditional, qualified, controller-relative, multiple-target, damage-linked,
delayed, and linked-result forms remain outside this family.

Fixed mass-damage lowering uses the same complete `ObjectQuerySpec` descriptor
consumed by the runtime affected-set snapshot. The compiler emits ordered
player/permanent groups and an optional exact target-opponent controller; it
does not encode card names or reparse Oracle text during resolution. Only the
closed positive predicates represented by the object-query vocabulary are
accepted. Negative keyword or subtype predicates, divided or variable damage,
multiple damage clauses, and linked result riders remain source-spanned
residuals until their own typed families exist.

Fixed additive damage replacement lowers `that much damage plus N` through the
shared damage-quantity capability and the v2 runtime component. The descriptor
contains only a positive fixed addition, controller relations, closed source
color or OR-type predicates, target scope, combat scope, and whether the
replacement source itself is excluded. Runtime applicability consumes the
immutable current damage-source snapshot; Jaya and Torbran therefore share one
generic owner without name dispatch. Dynamic additions, open characteristic
alternatives, conditional duration, and source predicates outside the closed
vocabulary remain source-spanned residuals.

Source-self wording uses one immutable `SourceReferenceSpec` across represented
counter, damage, prevention, trigger, entry, activation-cost, and declaration
grammar. It accepts the full Oracle name and bounded complete leading forms
before a comma, the title delimiters “the” or “of,” or a bounded ordinary
two-word name. The same owner recognizes a closed compile-time vocabulary of
`this` permanent descriptors, including Aura, Equipment, Saga, Spacecraft, and
Vehicle. Those descriptors identify the physical source; they are not current
characteristic predicates, so type-changing effects do not retarget or cancel
an already represented result. The model never guesses an arbitrary prefix,
suffix, nickname, or subtype. Lowered instructions use `$source`; runtime
handlers do not receive names or reinterpret Oracle text. Closed source attack,
block, damage, and intervening entry subscriptions use the same reference
vocabulary. Fixed named self power/toughness and keyword results use the
existing `$source.zone_object` characteristic owner, so control changes retain
the object and departure/reentry cannot retarget a queued modifier. Unknown
event tails, nicknames, durations, costs, and whole-card siblings remain
residual; existing exact descriptors keep precedence. See
[ADR 0040](../adr/0040-closed-source-self-references.md).

Fixed self power/toughness and supported-keyword modifiers compile through the
dedicated `query_characteristic_templates` owner and lower to
`QueryCharacteristicModifierSpec`. Controller,
opponent, and global object sets use `ObjectQuerySpec`; attachments, source
counters, and raw controller hand size use closed quantity scopes. Counted
objects are evaluated only through layer 5 before concrete layer-6 and layer-7c
effects are added, so represented type and color changes feed the count without
later-layer recursion. Current compilation uses the source's current controller
for “your” zones. Fixed public battlefield and graveyard thresholds accept
prefix or suffix self wording and may add only power/toughness, only supported
keywords, or both through that same descriptor. Inverted existence wording,
non-source subjects, and ability removal remain residual; this grammar adds no
family-specific layer-6 applicability check. The earlier enum fragment remains
replay-readable only. See
[ADR 0088](../adr/0088-typed-query-self-characteristics.md).

Required and excluded keyword predicates are both outside this layer-5 quantity
boundary. Typed descriptor decoding rejects them before counting, including for
a known-empty collection; they cannot silently produce a zero result.

`compiler/declared_effect_amounts.py` binds ordinary announced spell-cost X or
one complete public count definition to numeric result slots in existing typed
instructions. Two fixed-value compilations must retain identical operations,
target schemas, and dependencies; only supported result fields may vary. The
original fixed template identity is retained for runtime admission. Public
declarations carry explicit Oracle-node-scoped identities and persist their
first execution-time value across sibling results and replacement resumption.
Independent quantity expressions retain their separate execution-time reads.
One fixed counter placement may consume that existing public-query amount
beside independently closed ordered components. The compiler preserves the
original placement subject, counter kind and target schema; the canonical
placement owner receives the concrete execution-time integer. Unrepresented
activation costs still block an otherwise recognized result.
The casting owner remains the sole authority for choosing and paying cost X.
Target-qualified spell-cast predicates consume version-six public cast facts.
The casting owner reads effective creature types and controllers only for
selected targets still in their expected zone and incarnation at cast completion
(CR 115.9b); phased-out or departed objects are ignored. One qualifying cast
produces one occurrence, regardless of the number of creature targets. Aura
spells may satisfy the any-spell form, while ordinary copies remain separate
copy events. Targets-only, target counts and first-per-turn variants remain
outside this boundary.

The physical spell composer preserves independent public permanent, player and
stack-spell target roles across up to four printed clauses. Bounded optional
creature groups retain their own selected list through scoped group references,
so an empty or partly invalid Support group does not shift a later scalar target. Each role can select the same object
as a different instance of the word target, and partial target invalidation
retains only the applicable instruction. Component validation verifies the
exact target-role index and source span before admitting the combined program.
An exact recognized clause whose group cannot produce a primary executable
spell carrier adds a material lowering residual; an empty carrier cannot become
capability-closed merely because its leaf recognition succeeded. Mixed-role programs reuse the existing target, hand-choice, counter, control and
characteristic owners. Unsupported group domains remain blocked until their
composition is owned; every emitted multi-target program must also pass the
existing closed-effect composition validator.

`compiler/permanent_additional_cost_nodes.py` lowers one mandatory printed
casting-price clause on a permanent into its primary spell cost schema. The
carrier has no resolution effects and uses the normal battlefield destination;
the casting owner applies the existing exile, discard, sacrifice, owner-hand
return, life, counter, or binary alternative price. Whole-card admission remains
required. Repeated declarations and independently unrepresented siblings retain
material residuals, so a supported price cannot make an unsupported card exact.

Single-subject `place_counters` results also bind declared or current public
characteristic amounts through their existing counter owner. A resolved zero
amount is a paid no-op; malformed and negative amounts are rejected. Counter
source attribution stays attached to the original resolving instruction. Group,
set, and player counter amounts remain outside this declared-amount boundary.
Attached and targeted regeneration nodes retain the registered shield handler
dependency beside the fixed-effect capability, including when a newly closed
event-return sibling makes the complete card reachable.
Current-program assembly supersedes an identical reviewed self-graveyard return
with the trusted event-bound return only when event shape, source hashes, costs,
targets and handlers agree. Both assembly paths share this precedence; unrelated
legacy abilities remain separate and one printed return queues one trigger.
Combined deliveries use a registered aggregate probe over original whole-card
programs. The probe deduplicates Oracle IDs and retains independent residual
siblings; its generated transition receipt binds the original immutable base
frontier across revisions of an unmerged batch. Final PR delivery still uses
immutable base/head corpus receipts and subtracts lost support. Interaction
coverage distinguishes actual Aura, shroud and target composition from explicit
rejection of unresolved original-card siblings.
Undefined X, X-dependent target domains or cardinalities, variable activation
costs, nonordinary X mana costs, linked or source characteristics, open arithmetic,
unrepresented result owners, and nested modal declarations remain residual.
Ordinary Cycling explicitly
declares the existing draw handler dependency beside its activation capability;
runtime binding still rejects undeclared registered dependencies.

`compiler/effect_template_composition.py` routes reviewed atomic effects into
closed clause/program composition and gives query-derived amounts one shared
entry point. `compiler/public_query_effect_amounts.py` reuses that same typed
quantity for one standalone resolution instruction across spell, activated,
normalized trigger, and already-typed quoted-activation contexts. The compiler
accepts only a fixed coefficient over a controller or global battlefield,
graveyard, or identity-free hand query when replacing the amount with a fixed
integer produces an already closed life, damage, draw, token, or
source/direct-target temporary power/toughness operation and target schema. A
power/toughness result may retain fixed supported keywords beside the
query-scaled modifier. It serializes each quantity as a semantic scalar value
rather than a new effect operation.
Ordinary program registration validates the same scalar projection and the
unchanged fixed effect and target shape before promoting a spell or activation.
It requires the quantity capability and the full original dependency closure;
a scalar descriptor alone cannot grant trust.
Source-self results lower through the existing source-characteristic operation
and logical-object-aware source reference. A departed or re-entered source
cannot receive the old ability's modifier. A valid quantity resolving to zero
is a no-op after payment; malformed fields remain rejected. Historical records
retain their saved scalar operations and source references rather than being
recompiled on load; records declaring superseded exact-runtime trust provenance
are explicitly incompatible under the Game Record contract.
`semantic_runtime/query_effect_amounts.py` validates and resolves that scalar,
and `semantic_runtime/values.py` supplies the stack object's locked controller
to the cycle-safe layer-5 quantity evaluator immediately before the unchanged
operation handler runs. Copies therefore use the copy's
controller, later public type and color changes affect the count, and existing
target revalidation, damage prevention, life replacement, draw coordination,
token replacement, current granted-ability applicability, layer-7c ordering
and cleanup, projection, and replay remain authoritative. Source-counter,
attachment, opponent-relative, source-excluding, ability-presence, stateful or
later-layer quantities, linked, chosen, optional, modal, conditional,
aggregate-set, multiple-target, variable-duration, unsupported quoted or
granted, and open-arithmetic forms remain source-spanned residuals.

`compiler/scalar_effect_amounts.py` extends the existing numeric result
projection with complete referenced-characteristic, committed-event, and
represented current-turn definitions. Its fixed-value differential proof
retains the operation, target schema, and dependency owner; only result slots
receive scalar data. Explicit source references, singleton direct battlefield
targets, and represented zone-event objects remain distinct origins. Ambiguous
pronouns, optional target references, X-dependent targets or costs, aggregate
extrema, chosen facts, and independently unsupported bodies remain residual.
Declarations receive Oracle-node-scoped identities; separate declarations
cannot share a cached value. Ordinary negative result amounts use zero before
the printed result sign, while known zero and unavailable information remain
different outcomes. Doubling/base-setting exceptions are outside this grammar.

Public event-effect triggers compile only when one closed normalized carrier
and one independently exact typed body compose through the shared event-effect
owner. Zone events consume transaction-sealed owner, prior controller, type,
subtype, supertype, color, keyword, mana-value, token, attachment, and integer
power/toughness facts; damage and draw use
committed result events; spell-cast schema v5 adds committed public target
references while retaining historical schemas. Completed attack, block,
Cycling, face-up, and spell-copy actions share one public-action occurrence
capability and the ordinary APNAP batch. Ordinary and Storm copies dispatch
through one normalized spell-copy adapter. Strict Heroic, Magecraft,
Constellation, Battalion, current equipped-attacker, exact enchanted- or
equipped-damage, attached-creature death, bounded public entry/departure,
controller-another Cycling, fixed step, and one-or-more creature-card graveyard
departure carriers reuse these events, the shared current layer-6 ability
query, and independently exact noncounter effect bodies. The one-or-more form
deduplicates only inside the canonical simultaneous trigger batch. Block
declarations emit one blocks occurrence per assignment but only one
becomes-blocked occurrence per attacker. Counter bodies, source-tapped
producers, broader attachment relations, Room, dice, chosen,
history-relative, other targeted-event, declaration-replacement, ambiguous
disjunction, linked-event, and dynamic-comparison grammar remains residual.
The same owner now accepts a second bounded public-action closure: controller
attack declarations with zero, two, or three minimum attackers; typed
discard and sacrifice subjects; fixed token, land, creature, artifact, and
graveyard entry or departure predicates; controller Cycling; source damage to
a player or a planeswalker or battle; opponent noncombat damage; and exact
one-or-more attack, combat-damage, token-entry, and graveyard-departure
batches. These predicates consume only the existing attack, damage, Cycling,
and cause-preserving zone occurrences. First-time, once-per-turn, aggregate
sacrifice, tap-state, source-token damage, chosen, modified, and
counter-bearing public-action forms remain residual.

`compiler/qualified_zone_event_bindings.py` adds closed single-object public
entry, death, graveyard, and leave subjects through that same event-effect
owner. It normalizes subject-word order into the existing characteristic query
parser and emits only ordinary declarative occurrence predicates, not another
targeting check. Fixed type/subtype unions, color, supertype, keyword, mana-value,
and individual power/toughness comparisons may compose with controller, token,
source-exclusion, and graveyard-owner facts. A source-or-another union preserves
the independent source branch; existing productions retain precedence and their
serialized descriptors. Superseded reviewed execution views retire when this
generic production owns the same printed ability; current games never execute
both, while saved historical registries retain their pinned compatibility data.
Entry reads committed current characteristics and
departure reads sealed battlefield LKI, including when the observer also leaves.
The additional `trigger.event.counter_qualified_zone_change` capability admits
one fixed named-counter minimum-one predicate. Version-two zone occurrences
seal separate previous and current counter maps: departures retain counters
before zone reset, while entries read the new incarnation after entry
replacement and initialization. Version-one occurrences preserve their payload
and omit these unknown facts; a counter-qualified subscription requires sealed
facts rather than consulting the current object. Known-empty maps remain valid.
The shared layer-6 ability query controls discovery and the existing APNAP,
target, effect, replacement, and replay owners execute the resulting trigger.
Aggregation, cross-zone card subjects, hidden or chosen information, other counter,
combat-state, damage-history, relative and total-stat queries, and independently
unsupported event or body forms remain residual. See
[ADR 0090](../adr/0090-typed-public-event-effect-triggers.md).

`compiler/keyword_event_effect_nodes.py` owns the isolated fixed printed
Afflict, Annihilator, Firebending, Ingest, Mobilize, and Soulshift grammar.
Each keyword becomes an ordinary source-spanned trigger over the existing
block, attack, committed-damage, or zone-change event rather than a parallel
keyword engine. Afflict and Annihilator consume the sealed defending player;
Firebending records its surviving mana in the canonical provenance lot owner;
Ingest moves the damaged player's current library top through the zone-
replacement owner; Mobilize resolves the immutable token replacement batch
before issuing one private destination choice for every resulting attacking
token, then commits the complete group simultaneously; and Soulshift uses
the existing graveyard target, optional effect, and last-known-controller
owners. Variable, repeated, combined, granted, copied, conditional, and
independently incomplete forms remain source-spanned residuals.

`compiler/combat_entry_activation_nodes.py`,
`compiler/cast_lifecycle_nodes.py`, and `compiler/myriad_nodes.py` share the
bounded fixed combat-entry lifecycle family. Isolated ordinary-mana Ninjutsu,
Commander ninjutsu, Encore, Blitz, Sneak, and Web-slinging plus bare Myriad
lower through the existing activation, casting, normalized attack-trigger,
token, combat-recipient, and delayed-transition owners. Ninjutsu and Sneak
freeze the returned unblocked attacker's recipient before paying the cost.
Myriad freezes copyable last-known characteristics when it triggers. Encore
records its post-cost public source zone and copiable values, uses current
information only while that exact incarnation remains there, and otherwise
consumes the sealed last-known snapshot. Its per-opponent and replacement-
expanded copies enter through one grouped token event. One delayed trigger
filters those recorded incarnations by current zone and original controller,
then submits every eligible copy to one simultaneous sacrifice through the
canonical replacement-aware zone owner. Archived pre-v220 Encore records used
independent per-token cleanup triggers; the current replay path rejects their
runtime-trust identity instead of decoding them as the grouped result. A
resolved Blitz permanent records a
versioned, incarnation-pinned designation for the exact compiled ability.
Its graveyard trigger consumes predeparture ability applicability and controller
facts from the normalized permanent event, so ability removal suppresses the
draw while loss of creature type does not. The independently created delayed
sacrifice remains controlled by the caster and moves the permanent only while
that player still controls the same incarnation; historical v1 designations
and delayed instructions retain their serialized behavior. Variable, modified,
repeated, granted, copied, team-specific, and independently incomplete forms
remain residual. See
[ADR 0102](../adr/0102-typed-fixed-combat-entry-lifecycles.md).

The generic typed token-quantity replacement component copies the current
immutable token specifications, including Myriad's selected-opponent
association, before token identities or timestamps exist. Myriad first
collects its optional per-opponent creation decisions, resolves replacement
ordering without mutation, then asks independently where every resulting copy
attacks within that opponent's legal player/planeswalker set. The canonical
token owner reruns the pinned selection journal and commits the expanded group
with one timestamp; delayed cleanup remains pinned to every created
incarnation. Printed token-quantity replacement grammar remains residual.
Historical one-stage Myriad continuations retain their schema-v1 completion
path.

`compiler/ward_cost_templates.py`, `compiler/ability_keyword_fragments.py`,
and `compiler/continuous_templates.py` own the fixed public Ward closure.
Historical fixed-generic Ward fragments retain schema version 1. Fixed positive
life and discard-one payments use schema version 2, while fixed-generic Ward may
compose with supported printed keywords or the existing query and attachment
layer-6 grants. Trigger discovery still reads only current effective fragments
and emits ordinary APNAP occurrences; one semantic-choice owner delegates the
typed payment or decline to canonical mana, life, discard, and stack-counter
intents. Static grants use the shared ability-presence applicability query, not
a Ward-specific check. Random, qualified, multiple-card, alternative,
sacrifice, composite, dynamic, and granted nonmana costs remain residual. See
[ADR 0103](../adr/0103-typed-fixed-public-ward-payments.md).

Fixed multi-event trigger subscriptions reuse that same owner without creating
a synthetic event. One source-spanned ability may name exactly two represented
events in the battlefield active zone; the compiler serializes an ordered
`FixedEventSubscriptionSet` with one closed condition per event and one shared
typed effect body. Trigger discovery selects the matching subscription from
the committed occurrence, applies the shared current layer-6 ability query,
and places the ordinary APNAP trigger. The closed grammar covers source or
exact printed-name entry, attack, death, leave, graveyard, face-up, combat-
damage, monstrous, block, and sacrifice pairs plus the exact current enchanted
creature attack/block relation. Cross-zone Cycling/death pairs, commander and
Room subjects, linked Haunt, chosen or another-object predicates, unavailable
event producers, and more than two events remain residual.
`card_programs/reviewed_overlay.py` owns the single precedence query shared by
CardProgram compilation and live generated-program registration. It removes a
reviewed split only when every subscription has one trusted, body-identical
source-self counterpart, preventing duplicate triggers without card identity
dispatch.

`compiler/static_cast_rule_templates.py` and
`compiler/activation_restriction_templates.py` own the closed battlefield-
static action-legality grammar. They lower fixed public spell queries into
instant-timing permissions, cast prohibitions, one-spell-per-turn limits, and
spell or ability counter prohibitions; the same family lowers fixed public,
chosen-name, enchanted-object, and enchanted-player activation prohibitions.
The exact enchanted-permanent untap/prohibition compound composes the existing
untap participation capability with the activation restriction instead of
inventing a second untap rule. Every emitted node requires the shared current
layer-6 static-component fragment. Temporary and targeted rules, cost changes,
free casts, dynamic quantities or comparisons, linked objects, spell-self
riders, Split second, Epic, grants, copies, text changes, and unsupported
compound siblings remain source-spanned residuals.

`compiler/fixed_source_combat_growth.py` owns the narrower source-self combat
growth body grammar. It accepts only mandatory fixed integer power/toughness
changes until end of turn or exactly one +1/+1 counter after the source
attacks, blocks, becomes blocked, blocks a creature with Flying, or deals
combat damage to a player. Attack, block, and committed damage producers remain
authoritative for the occurrence; the flying predicate reads the blocked
attacker's sealed public keyword snapshot. Both effects use
`$source.zone_object`, so resolution affects only the same current battlefield
incarnation, and discovery uses the shared current layer-6 ability-component
query. Optional, payment, dynamic, aggregate, combined-event, targeted,
multi-effect, other-counter, non-self, and broader damage forms remain
source-spanned residuals.

## Extending the compiler

Add the smallest reusable grammar production and typed construct. Include
source-pinned positive, negative, ambiguity, residual, canonical-JSON, and
runtime-lowering tests. Reuse an existing runtime primitive when its contract
matches exactly; otherwise leave the construct residual until the primitive
has its own owner and assurance. Regenerate the compiler and rules reports
through their owning commands rather than editing them by hand.

Schema changes and new stage ownership require an ADR. Current coverage and
blockers live in the generated
[compiler status](../COMPILER_COVERAGE_STATUS.md); stable IR fields and
provenance live in the [Oracle IR reference](../reference/oracle-ir.md).
See [ADR 0005](../adr/0005-card-program-v2.md),
[ADR 0006](../adr/0006-typed-semantic-handler-boundary.md), and
[ADR 0022](../adr/0022-reusable-rules-piece-inventory.md).
