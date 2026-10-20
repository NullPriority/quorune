---
title: "CardProgram runtime components"
status: "current"
authoritative_source: "quorune/semantic_runtime component registries and schemas/card-program-v2.schema.json"
verified: "2026-09-17"
audience: "rules, compiler, runtime, replay, and extension contributors"
maintenance: "hand-maintained"
---

# CardProgram runtime components

Runtime components represent CardProgram behavior that participates outside a
single immediate resolution instruction. Typical families discover immutable
replacement effects, prevention effects, draw policies, or continuous effects
from live source descriptors. Components participate in a subsystem-owned
transaction; they do not commit state.

Public power/toughness aggregates use the immutable
`PublicCharacteristicAggregateSpec` and the read-only owner in
`quorune/public_characteristic_aggregates.py`. Version-3 scalar amounts and
self-cost metrics carry the complete battlefield query, controller relation,
stat and sum/maximum operation. Effects and mana use current complete layer-7
characteristics; casting offers and accepted commands use the same producer.
Negative members enter a total before an ordinary result is clamped to zero.
A declared scalar is cached once for its resolving ability, including later
instructions that change the candidate stats. Known empty sets yield zero;
unavailable creature stats raise an error. Hidden zones, source exclusions,
attachment-state predicates and static characteristic cycles stay outside this
family. The aggregate capability remains an explicit dependency of each consumer.

Quoted ability grants compile their complete child through the same fixed and
ordinary-source trigger paths as standalone abilities. The ordinary fallback
runs only when the first compiler returned no node and no residual. Entry and
death subscriptions retain source identity, and recipient control determines
their consequences. A quoted source-permanent regeneration instruction uses
the existing incarnation-pinned shield owner; the grant origin never substitutes
for its recipient. Unsupported child clauses still prevent whole-program trust.

An independently source-specific action/damage subscription may lower a whole
`it deals N damage` instruction through the existing fixed damage leaf. Public
event antecedents and newly created object references remain excluded. Trigger
targets still use the shared announcement and resolution revalidation owner.
Fixed source-return entry subscriptions declare `permanent.return.owner_hand`
alongside their return-choice family so registered bounce intents retain strict
capability closure.

Committed scalar amounts accept the source-specific `damage.dealt.self`
subscription identity without normalizing it away. Broad source damage may
target a player or permanent; incoming combat damage retains both the receiver
identity and combat predicate. The scalar result reads the committed event
amount after prevention and replacement. It does not read current power, life,
or the original damage request. Ordinary draw, token and signed life result
owners still provide their own closed schemas and capability dependencies.

Fixed opponent mill carries the closed `controller_opponents` recipient scope.
The mill handler filters the current APNAP roster against the resolving
controller and submits one batch to the existing mill transaction. Empty
opponent rosters produce no intent; short libraries contribute only available
cards. The historical `all_active_players` scope retains its recipient contract.
No pre-mill hidden card identities enter the descriptor or public projection.

Event-addressed mill requests `zone.mill.event_recipient` through the dedicated
`fixed-event-recipient-mill` mechanic. A damage binding must guarantee a player
recipient before using `$context.target`; source attacks use the sealed
`$context.defending_player`, and spell casts use `$context.player`. These
references create no target announcement. Fixed and committed scalar amounts
retain their own producer schemas. A context reference without this mechanic,
an ambiguous event or an unsupported result tail prevents exact admission.

Fixed event-addressed draw and life results use the same public occurrence
boundary with a separate `resolution.event_player.fixed_result` dependency.
Spell casts, draws and public scry/surveil events supply their player field;
discard occurrences supply `previous_controller`. Damage must guarantee a
player recipient and individual source or public attacks supply their defending
player. Entry events supply the entering `controller`; death events supply
`previous_controller`. Aggregate attack events do not supply a unique defender.
Each subscribed occurrence must supply the same referenced player field before
program promotion. One or two complete
fixed leaves preserve printed order and require an explicit subject per clause;
`that player` addresses the occurrence's player, while `you` retains the locked
trigger controller, including after a change of control of its source.
`That creature's controller` is an object-information request under CR 608.2h.
Only a death occurrence supplies that departed incarnation's controller through
`previous_controller`; re-entry creates a new object and does not replace it.
Other events keep this subject residual because the family does not represent
the required current or last-known object-controller reference. Optional, variable and linked
results remain outside this family. Fixed affected-player
discard registration separately verifies its closed choice shape and declared
dependencies before promoting a generated program.

Fixed static prevention version 2 uses the existing extended damage source
predicate for printed color restrictions. Source colors come from the frozen
damage proposal; matching damage events each receive the printed prevention
amount independently. Current control, source ability presence and departure
still determine participation. Version-1 descriptors retain their original
schema. This family does not consume a persistent prevention pool or interpret
chosen-color and variable prevention text.

Fixed entry-event counter results carry `$event.entry_object` and request
`counter.producer.fixed_event_object` alongside ordinary counter placement.
The reference resolves only the public battlefield object with the entry
occurrence's sealed identity. Departure, phasing and re-entry make the old
reference unavailable, so the existing resolution boundary skips that leaf.
The observer remains the placement source and never replaces the recipient.
Aggregate, multiple-event, death and partial-result antecedents stay excluded.

Individual attack and qualifying damage-source counter results use
`$event.counter_object`. Discovery seals the attack participant or frozen damage
source identity in a separate event-counter snapshot before collecting observer
LKI. Resolution uses the same public incarnation check as entry references.
Fixed counters and committed scalar counts share ordinary counter placement;
the triggering observer never substitutes for the attacker or damaging source.

Source damage subscriptions may bind a complete fixed `put ... counters on it`
instruction through the existing `$source.zone_object` leaf. This preserves
source specificity and incarnation identity. Damage and state-based actions
finish before trigger resolution; counters do not rescue a creature that already
died, and a re-entered source cannot receive the old trigger's counters. Public
damage-receiver pronouns without a represented recipient remain excluded.

Fixed counter affected sets admit one positive attacking or blocking state
predicate over creatures. Selection uses current combat membership when the
instruction resolves. Tapped state and earlier attack history do not substitute
for that membership, and additional unrepresented state qualifiers remain
excluded. The ordinary counter transaction and state-based counter cancellation
retain ownership of placement consequences.

Fixed activated mana version 3 carries one canonical selected sacrifice or tap cost
alongside its closed fixed output modes. The compiler requests the selected
zone-change capability and the mana owner separately. Offers and accepted
commands use the existing typed activation transaction to validate and pay the
controlled object cost before adding the chosen mana once, without a stack item.
Selected tap costs retain fixed counts and current typed queries. A combined
source `{T}` cost excludes the source from the selected group; summoning sickness
limits the source tap symbol but does not prevent paying a selected-creature tap.
Legacy versions retain their output and result-rider schemas. Multiple selected
costs, discard or exile costs, and selected-cost result riders remain excluded.

Fixed energy activation clauses decode before generic mana-symbol stripping.
Each literal `{E}` contributes one counter; the existing counted-word format
retains its typed amount and payload. Multiple exact clauses add their amounts.
Mixed, stray, variable and alternative energy syntax stays unresolved. The
activation owner uses `ActivatedAbility.energy_payment` for both affordability
and its canonical player-counter transaction. Energy belongs to the activating
player and persists through phase boundaries. A source tap symbol retains its
usual control-duration and haste restriction, and countering the activated
ability does not refund either cost.

Ascend has separate typed permanent and spell paths. A current trusted static
component awards the controlling player the city's blessing when that player
controls ten phased-in permanents. The spell instruction checks only when it
resolves. One rules owner stores the persistent public player designation;
it is not a counter and does not transfer or disappear with a source. Zone
facts, complete simultaneous moves, control changes, instruction completion
and state-based stabilization synchronize acquisition before subsequent trigger
checks. Read-only snapshots and projection only observe the marker. The shared
public-state condition supplies its current source controller's designation
to existing characteristic owners; unresolved follow-up bodies remain residual.
The no-parameter city-blessing activation restriction reads the same designation
for the current activator during both offer and acceptance. Its typed condition
and capability retain the existing activation transaction and pending result;
sacrificing an Ascend source cannot revoke an acquired player designation.

Public resolution-condition schema 3 represents a fixed self-replacement with
independently closed default and alternative programs. The resolving controller's
condition selects one program before the original instruction or its external
replacement owners run. Earlier mandatory instructions remain a separate prefix.
The compiler binds a bounded repeated creature-set antecedent to its original
controlled set; optional replacement choices, changed targets and variable
results remain residual. Unversioned prefix conditions and schema 2 kicked-cast
facts retain their existing payloads and execution paths.

Normalized public events compose fixed untargeted permanent-set counter results
through a separate precise producer. Complete public-condition branches must
each contain an independently closed fixed set; a coarse counter mechanic alone
cannot cross the event-result guard. The existing affected-set and replacement
owners use the locked trigger controller. The event's attacker or entry object
does not become the recipient set or replace that controller. Current ability
components gate future discovery, while an already created trigger survives
its source leaving.

Fixed random-discard activation costs use an optional typed scalar that is
omitted from older ability payloads. The activating player must hold the full
count. The canonical random selector binds a distinct hand subset to the
server's existing payment identity, source incarnation and ability. It then
uses the exact counted activation discard transaction, including singleton
costs. A suspended replacement choice retains the selected refs privately;
resume recomputes and validates the same subset. The client supplies neither
selected cost cards nor a random seed. Earlier selected discard costs keep
their existing interface, and unsupported grants, mana activations or combined
object costs cannot drop the random cost during compilation.

The compound self-entry handler combines a fixed tapped state and fixed counter
creation in one replacement effect for complete “enters tapped with” wording.
It inherits the ordinary self-entry counter validation and submits both
operations to the existing zone replacement transaction. The printed ability
therefore keeps one ordering identity. Legacy fixed self-entry counters retain
their original untapped-state contract, and variable or linked entry tails
remain outside this compound family.

Current object-count quantities admit a fixed tapped or untapped qualifier on
public battlefield queries. The quantity owner reads the exact current flag
with the existing layer-5 type/color view, while consumers retain their own
closed schemas. Hand and graveyard state queries, historical tap events and
collection reductions with additional unrepresented state remain excluded.

## Lifecycle and participation

1. The compiler or reviewed program emits a versioned component descriptor.
2. CardProgram loading validates the descriptor against its registered family.
3. Strict preflight binds the descriptor and capability dependencies to the
   program, component inventory, and game-record fingerprints.
4. The owning subsystem discovers active descriptors through a bounded
   read-only context.
5. A family handler lowers each descriptor to an immutable participant.
6. The subsystem applies ordering and choices, then its canonical mutation
   owner commits the validated plan.

A source leaving its active zone, phasing out, changing incarnation, or
failing its declared predicate stops participating according to the family
contract. Discovery must be deterministic and may not expose facts outside the
requesting principal's authorized projection.

The layer-6 component-presence scan asks the registered continuous handler
whether a descriptor can affect static components before evaluating its live
condition. Every excluded public-state modifier validates its complete descriptor
and contains only ordinary keywords and power/toughness bonuses. Conditional
fragment variants remain conservative candidates and validate once through their
ordinary lowerer. Handlers without this query,
and subclasses with a different lowering implementation, remain conservative
candidates. Ordinary characteristic collection still evaluates every applicable
condition; each invocation reads current descriptors and state.

Live subtype queries preserve noncreature Kindred recipients when the source
uses a bare creature subtype. An explicit creature noun retains that type
restriction. Keyword grants and fixed modifiers share the query, while a
modifier leaves an undefined noncreature power or toughness undefined.
Records carrying earlier runtime-trust identities remain descriptor-readable;
the existing provenance check determines whether their execution is compatible.

Current program assembly replaces a narrower reviewed subtype anthem when a
trusted corrected generic descriptor owns the same source-pinned instruction.
Oracle and ruling hashes, controller scope, event, costs, targets, effects and
modifier must match. Source exclusions and distinct generated abilities remain
separate. Both CardProgram assembly and live registration use the same reviewed
overlay boundary; archived payloads retain their recorded descriptors.

## Descriptor and registry contract

`continuous.characteristics.fixed-public-setting.v1` applies one constant
setting to a reciprocal attached object or a closed public battlefield query.
The compiler preserves type retention, source exclusion, controller scope,
fixed colors, keyword additions, ability removal, base power/toughness and
fixed modifiers. Attached and live-set producers share the fixed keyword
vocabulary. Printed and granted Skulk use the existing blocking owner, which
compares the holder's and blocker's current power when blocks are validated.
Pure fixed P/T and keyword modifiers also apply to attached permanents without
changing their types. A noncreature's P/T modifier is retained for any later
creature state; an independent base-stat animation applies before that bonus.
Creature subtype replacement removes only creature subtypes,
preserving unrelated artifact or enchantment subtypes. Unknown subjects,
chosen values, names, dynamic definitions and quoted grants remain residual.

The setting handler and the existing attached-characteristic handler share
one pure layer emission owner. It produces the represented layer 4, 5, 6, 7b
and 7c operations for the existing evaluator. Ability removal precedes the
same setting's keyword additions; base settings precede modifiers and counters.
The ordinary timestamp and component-presence owners determine current source
participation. Source departure or phasing ends a static effect, and reciprocal
attachment identities prevent it from following a different incarnation.
Printed and copied values remain separate from these live characteristics.

`continuous.characteristics.conditional-creature-type-removal.v1` removes only
the source incarnation's Creature card type in layer 4 under a represented
public condition. Correlated creature subtypes disappear unless a remaining
Kindred card type still shares them. Its source ability participates through
copy, control and text effects; a later ability-removal effect does not undo
the earlier type change. Other component owners retain their ordinary
participation boundary. Missing condition information and malformed descriptors
fail closed.

The entry-replacement snapshot projects this typed self effect against the
pre-entry battlefield without inserting a live object. The incoming permanent's
mana symbols do not contribute to that devotion read. After entry, the ordinary
characteristic and event owners include the new permanent, so pre-entry
replacement classification and post-entry creature triggers can differ.

`ability.activated.mana.public-quantity.v1` shares the existing activated mana
registry and fixed-family cost boundary. Its versioned output reads a closed
public zone quantity, source counter quantity, source power or toughness, or
public turn history. The activation owner pays costs first, captures source
information immediately before a source sacrifice, and resolves the output
without the stack. Controller-relative quantities use the activating player;
source characteristics use current information or the retained departing
incarnation. Unknown information fails closed, while known zero and negative
result amounts add no mana and still pay legal activation costs.

Fixed-color, any-one-color, and allowed-color combination outputs validate the
complete current amount. Combination choices carry allowed colors and a total
instead of enumerating allocations. Source-sacrifice, counter-removal,
mana-priced, and combination abilities remain explicit activations so automatic
payment cannot skip their cost or choice authority. The same compiler amount
producers lower fixed-color spell instructions through the existing `mana`
effect owner; they read collections when the instruction resolves. Spending
restrictions retain the canonical mana provenance. Targeted or loyalty mana
abilities, linked sacrificed-object counts, unknown conditional tails, variable
allocation spell continuations, and unsupported sibling abilities remain
residual.

`replacement.zone.entry-designation.v1` represents a mandatory intrinsic
one-color or creature-type choice before battlefield entry. The existing
replacement journal retains the destination controller's selection, and the
card-designation intent owner commits it on the new incarnation. Copied
permanents make fresh choices; zone-object reset clears departing choices.
Aura spells select their enchant target before the entry choice and commit
attachment through the existing Aura owner. A designation does not change
ordinary targeting rules; chosen-type Shroud uses the shared offer and command
legality boundary.

`continuous.characteristics.chosen-designation.v1` binds a retained public
designation to a closed fixed-query characteristic descriptor. Its child
owner supplies the controller relation and layer-six or layer-seven-c result.
Unsupported chosen-value uses and incomplete parent cards remain residual.
Entry choices linked to a source-type addition remain with their existing
reviewed owner; the plain designation compiler contributes no second choice.
See [ADR 0106](../adr/0106-typed-public-entry-designations.md).

Every family declares a stable handler ID, schema version, event or layer,
rule references, capability dependencies, a strict descriptor validator, and
a deterministic inventory entry. Unknown registered fields, malformed values,
duplicate ownership, and unknown capabilities fail closed. The aggregate
registry provides discovery and a fingerprint only; family modules own
validation and lowering.

Default component construction validates its read-only capability and evidence
inputs once within an evidence-owner synchronous validation batch. Every family still checks
its registered dependencies against that validated snapshot. Nested batches
reuse the same inputs; completion or failure discards the snapshot, and a later
batch or ordinary load reads and validates current source and evidence again.
The snapshot contains no game state and is not a persistent registry cache.

Descriptors are part of the canonical CardProgram fingerprint. Historical
records may use an explicit compatibility adapter, but the adapter cannot
rewrite the recorded program or silently promote trust. Current records pin
the descriptor and registry directly.

`prohibition.combat.goad.controller-creatures.v1` represents only the exact
static sentence `Creatures you control can't be goaded.` Runtime discovery
requires a current trusted, selected-face-pinned battlefield program and emits
a typed public participant scoped to the source controller. Conditional,
duration-qualified, dynamically granted, and differently scoped prohibitions
remain residual rather than falling back to runtime Oracle inspection.

`casting.payment.affinity-artifacts.v1` represents each exact printed
`Affinity for artifacts` instance as a selected-face `cast.cost` descriptor.
Cost calculation counts only current phased-in artifacts controlled by the
caster through effective characteristics, reduces only generic mana, and
applies multiple printed instances cumulatively. Other Affinity parameters,
equivalent rules text, and dynamically granted or removed Affinity fail closed;
runtime code does not inspect Oracle text or keyword metadata as substitute
authority.

`modification.cast-cost.self-public.v1` and
`modification.cast-cost.fixed-query.v1` represent the bounded self and
battlefield-source cost families. Selected-face or current-component discovery
evaluates only public controller/opponent queries, public thresholds, source
counters, party, color and effective-name counts, total mana value, devotion,
Domain, life, and sealed turn-history facts. The typed query distinguishes
existential `an opponent` thresholds, evaluated per opponent, from aggregate
`your opponents` quantities. The result is a signed generic or colored vector
applied by the same total-cost query used for offers and accepted commands.
Target-relative prices, unrecorded history, dynamic power/toughness, chosen or
hidden facts, caps, floors, and open arithmetic fail closed rather than
becoming a runtime prose query.

An established source-self event can bind optional pronouns such as
"you may have it deal" to that source before composing the existing optional
result owner. The complete leaf retains its target restrictions and original
source incarnation. Targets are chosen when the original trigger is placed;
apply-or-decline is chosen when it resolves. Granted entry abilities belong to
their receiving permanent and its controller, including as that permanent
enters. A created trigger survives the live grant ending. New-object and
unestablished antecedents remain residual.

Fixed payment cost schema 3 adds positive fixed-count exile of owned public
graveyard cards. The existing object-choice owner offers the represented card
qualities and accepts the complete count or a decline. One canonical ordinary
zone move commits the selected cards to exile with destination replacements.
Stored card controller does not replace graveyard ownership. Cost schema 2
retains its existing encoding for earlier payment kinds. Ordinary conditional
results keep their original target timing; selected graveyard-return identities
survive a paid prefix, so a target exiled as the cost remains exiled. Explicitly
linked new-zone objects retain their separate reference ownership.

The same cost schema also represents fixed counts of controlled untapped
permanents. The compiler reuses the fixed activation tap selector, while
resolution keeps the trigger's locked payer. These worded costs can tap newly
entered creatures. `another` excludes the original source incarnation through
`$source.zone_object`; a returned new object is distinct. The canonical tap-set
intent commits every selected member before discovering tap triggers. Tapping
an attacking creature does not remove it from combat. Variable, linked and
source-self tap costs remain residual.

Cost schema 4 adds fixed removal of one named counter from the original source
permanent. Its explicit source reference resolves through `$source.zone_object`;
leaving and returning cannot substitute new counters for the old object. The
choice rechecks the full counter amount and retained source identity before
emitting the canonical counter-removal intent. The payer stays the trigger's
controller, while an unqualified source-local removal remains available after
the source changes controller. Cost pronouns are normalized only inside an
established source-self event. Earlier cost encodings remain unchanged.
Paid and optional legacy library searches pass their existing complete
battlefield or hand shape validator before ordinary search execution resumes;
counted-search schema 2 retains its existing model validation.

Resolution-time payment schema 4 composes one existing fixed action with a
`reflexive_trigger_program`. This immutable body retains unbound targets and
quantities until it resolves in its own stack frame. The existing payment
choice commits the cost and then enqueues a typed reflexive occurrence through
ordinary APNAP trigger placement. The creating ability finishes before targets
are chosen, and players receive the ordinary response window. Declining creates
no occurrence; an unavailable mandatory target does not refund a completed
payment. The trigger retains the creating ability's controller and source
incarnation. Repeated payments, nested optional costs and unrepresented
paid-object antecedents remain residual. Earlier payment schemas keep their
existing decoding and timing.
Source damage in these frames uses the shared current/last-known source owner;
a departed source's controller and keywords remain independent of the
controller of the reflexive ability.

`ability.activated.mana.color-set.v1` binds a compiler-pinned relative object
query to the activating seat and reads only matching public battlefield
objects or that seat's graveyard. It derives colors from current effective
characteristics, feeds manual offers and automatic payment through the same
mode set, and treats an empty qualifying set as a legal activation that adds no
mana. Runtime code does not parse Oracle prose. Wider dynamic or conditional
mana wording remains residual.

`permission.action.static` owns immutable controller- or table-scoped action
permissions discovered from current-face, trusted descriptors on phased-in
battlefield sources. Its closed handlers authorize an owned graveyard land,
controlled-creature activation through the existing as-though-haste owner,
current-library-top visibility and fixed land or spell access, or a fixed
number of additional land plays. Library access revalidates the exact current
top identity and cycle-safe effective public characteristics for both offers
and commands. Projection distinguishes a controller-private look from a
public reveal and exposes no deeper library object. Additional-land components
contribute to the current total allowance, while a versioned turn ledger counts
successful ordinary land plays independently of source provenance. Removing,
returning, phasing, or changing control of a source recomputes the allowance
without replenishing prior plays. Historical slot records adapt explicitly to
the ledger, and the existing turn owner resets both formats.
Every represented permission uses the shared current layer-6 component query,
so ability removal suppresses it without a family-specific presence check.
Discovery itself is read-only; ordinary timing, cost, control,
zone-transition, cast, land-play, and commit rules remain authoritative in
their existing owners. Unrepresented wording fails closed rather than falling
back to current Oracle text.

The pinned activation catalog carries optional strict `power_up` pricing data
beside its distinct once-per-incarnation limit. Existing fixed cost options
feed the source-aware price query, and the same payable alternatives drive
offers, selected-cost proposals and commit revalidation. Pricing reads current
mana cost and canonical battlefield entry history; it never reparses ability
text. The ordinary payment and usage owners commit together after validation.
Historical descriptors without this field retain their original shape;
historical record execution still requires compatible runtime trust provenance.

Fixed ordinary-mana Reconfigure uses two independent source-spanned activation
programs in the same catalog. Both require complete-card admission and sorcery
timing; attaching targets another controlled creature, while unattachment
requires a current creature attachment. Each action pays its own cost.
Current typed activation markers identify Reconfigure at the shared attachment
boundary, independently of Equip pricing and timing.
Runtime binding independently requires the Reconfigure capability when that
typed activation marker is present.

Attaching commits a layer-4 creature-type removal with an explicit
`until_unattached` duration. Its optional `attachment_duration` facts lock the
source and receiver incarnations and attachment timestamp. Losing abilities
does not end it. Control changes and phasing retain the relation; departure,
unattachment or a later attachment expires its applicability. Later animation
still uses ordinary state-based detachment. The zone/attachment family's
`unattach` operation delegates reciprocal mutation to the existing owner.
Older duration and activation payloads omit these new facts. Variable and
nonordinary Reconfigure costs and uncompiled attachment providers remain
residual. [ADR 0120](../adr/0120-attachment-bound-reconfigure-effects.md) records
the duration and operation boundary.

Dynamic self-entry counters also accept a fixed threshold of actual mana units
of one ordinary color spent on the original spell. An optional `mana_color`
qualifier belongs only to that amount source; other amount payloads retain
their existing shape. The handler requires complete-card admission and runtime
binding independently requires `casting.payment.mana_unit_counts` and
`counter.producer.paid_color_self_entry`.

The casting owner records a versioned immutable payment fact for every original
cast in current execution, so an entry-copy effect can acquire the ability
after payment. Counts use concrete paid mana types, including mana spent on
generic or additional costs. Noncast entries and spell copies supply no paid
cast; a current cast missing its required fact fails closed. The resulting
amount enters the existing self-entry and counter-replacement pipeline.
Ability-word labels add no behavior. [ADR 0121](../adr/0121-spell-payment-counts-and-entry-conditions.md)
records the payment, copy and historical-execution boundaries.

Resolution-condition schema six adds results selected by actual units of one
concrete mana type paid for the original spell. Schema seven selects a fixed
same-target replacement before either damage result is applied. Both use the
existing condition and child-effect registries and require independent payment,
condition, prefix and branch capabilities. Older condition schemas retain
their shapes. Copies have no paid mana; missing original facts fail closed.

Mandatory prefixes execute first. A represented single controller-life query
reads the resulting current battlefield, and a private selection starts only
after earlier draws finish. An information-derived target controller uses the
existing announced incarnation, current effective control or controller facts
captured at the canonical departure boundary. It adds no player target and
never follows a returned incarnation. Initial target invalidity still prevents
the whole spell from resolving. [ADR 0122](../adr/0122-paid-mana-resolution-and-target-information.md)
records the ordered program and information-reference boundaries.

Typed continuous-characteristic descriptors own the represented live-state
families. `continuous.ability.fixed-query-keyword-grant.v1` lowers closed
source-controller, source-opponent, or global battlefield queries into
immutable layer-6 effects. The same canonical query scopes fixed layer-7c
power/toughness modifiers and combined characteristic grants. It evaluates
type, subtype, color cardinality, token identity, and named +1/+1 counter state
from the current public characteristic boundary; opponent relations exclude
the source controller without enumerating seats in the descriptor. Each
compiled node also declares the exact combat, damage, destruction, or targeting
capability that consumes the granted keyword. Combat-state, ability-qualified,
dynamically counted, conditional, temporary, and unsupported-keyword predicates
remain residual. `continuous.characteristics.leveler-bands.v1` selects one of
two compiler-pinned contiguous Leveler ranges from the source's current public
level-counter count. A typed static component-scope fragment binds every child
program to the parent level symbol and removes the flattened band inventory
from base values before ordinary layer ordering. The shared component query
suppresses every child when the parent is absent. The Leveler component removes
only inactive child `StaticComponentSpec` identities and adds the active
keywords at the source timestamp in layer 6, followed by the active fixed base
power/toughness in layer 7b. Triggered and continuous discovery consult the
surviving component, while the same generic fragment-removal operation
suppresses its materialized activated ability. Copies carry the descriptor but
use their own counters and timestamp. Level Up remains an independent
always-present activation. Room, Prototype, arbitrary striations, unsupported
children, and dynamic count/type-change edges remain outside this boundary.
Ordinary Class level bars reuse the same component query with a typed minimum
public designation: the activation remains a normal current ability, while
each bar's separate static component exposes only its independently exact
children at level 2 or 3. Level advancement emits one source-self normalized
occurrence after the designation changes, so a newly applicable child trigger
uses ordinary APNAP placement. The reusable no-maximum-hand-size component
consults that same current-component snapshot during both cleanup advertisement
and command validation. The noncopiable designation survives type loss,
ability loss, control changes, and phasing on the same permanent and resets at
the canonical zone-object boundary. Every trusted battlefield
`characteristics.evaluate` program contributes one `StaticComponentSpec` to
the source's effective ability fragments. Continuous-component collection uses
that single layer-6 component-presence query for printed programs, explicit
typed grants, individual removals, and remove-all effects; missing or stale
semantic keys fail closed. The applicability snapshot is evaluated through
layer 6 with component filtering disabled only for that bounded recursive read,
so family-specific ability-presence checks are unnecessary. The
`ability.static.conditional-keyword.v1` and
`ability.static.dynamic-power-toughness.v1` fragment handlers preserve closed
self conditions and count-derived modifiers on the effective ability set, then
evaluate them from public battlefield or owner-graveyard state. Copies retain
the typed fragments and reevaluate against current state; phased-out sources
do not participate. Unsupported wording remains residual, and display Oracle
text never selects these behaviors.

`ability.static.colorless-characteristic-definition.v1` is the separate
all-zone Devoid boundary. The selected face supplies one closed copied fragment;
after layer-1 copy values, the characteristic evaluator removes every color in
layer 5 as a CDA. Later layer-5 additions may add color, and layer-6 ability
removal cannot undo the already applied color result. A keyword string or
matching display prose without the typed fragment is inert. Untyped grants,
text changes, face-down producers, and dynamic characteristic counts remain
outside trust rather than introducing a family-specific layer-6 applicability
path.

`ability.static.all-creature-types-characteristic-definition.v1` is the
parallel all-zone Changeling boundary. It adds the pinned CR 205.3m creature
subtype vocabulary in layer 4 as a CDA after copy values and before later
non-CDA type setting. Existing target, combat, trigger, and count consumers use
the rendered effective type line; the former combat-only Changeling shortcut is
not an authority. Face-down objects and untyped or text-changed producers remain
outside trust, and the definition performs no dynamic characteristic read.

`casting.bestow.fixed-mana.v1` validates one complete-card-bound fixed Bestow
descriptor. The casting owner alone turns it into an alternate cost, Aura type,
target schema, and identity-pinned preparation effect. Cost-option effects use
the same trusted stack continuation whether or not the permanent has a separate
spell-effect program, and cost calculation evaluates each option using its
server-authored spell type. Target revalidation, attachment, and
bestowed/unattached characteristics remain in their existing generic owners;
clients never infer Bestow from Oracle or reminder text.

`casting.morph.fixed-mana.v1` owns the bounded ordinary fixed-mana Morph
descriptor. One trusted source-pinned complete-card certificate exposes a distinct private
face-down cast offer and an identity-pinned `turn_face_up` priority action.
The face-down method applies its 2/2 colorless nameless typeless creature
values in copy layer 1b, survives only stack to battlefield, suppresses static
runtime components and printed trigger discovery while the source is face
down, and reveals through the projection and zone-journal owners. Turn-up
eligibility previews the same object's effective face-up keywords, so a
represented layer-6 Morph removal blocks payment. Arbitrary static ability
addition/removal, copy, dynamic counts, Megamorph, other face-down methods,
variable and nonmana costs, and residual turn-up behavior remain outside
trust; no family-specific Oracle interpretation supplies them.

`ability.activated.unearth.v1` owns the bounded ordinary fixed-mana Unearth
activation descriptor, and `generic.unearth.v1` lowers its return and delayed-
exile results to one typed intent. Runtime admission requires the compiler's
complete-card certificate, then uses the shared activation proposal and commit
owners. Resolution delegates the card move, zone-object Haste grant, public
noncopiable designation, self-replacement, delayed trigger, and CR 400.7 reset
to their canonical owners. Countered, stale, partial-card, copied, granted,
multiface, and nonordinary-cost variants fail closed without interpreting
Oracle or reminder prose.

`casting.kicker.fixed-mana.v1` owns one single fixed ordinary-mana Kicker cost
descriptor. It contributes an optional additional total-cost branch only when
the compiler's complete-card admission certificate is exact, and commit
revalidates the descriptor before payment. The selected cost records one typed
paid-Kicker spell fact that zone replacement and normalized entry events capture
before CR 400.7 reset.

`casting.alternative-cost.fixed-public.v1` validates one source-spanned closed
alternative-cost descriptor and requires complete-card admission. The casting
owner alone converts it into a cost option and revalidates its current ability,
public eligibility, fixed mana vector, and at most one typed nonmana payment at
commit. Freerunning, Prowl, Spectacle, and Surge consume canonical turn-history
events; plain forms may consume the active turn or a current controlled
basic-land subtype. The handler creates no casting-legality, payment, history,
zone-transition, or runtime Oracle-text authority of its own.

`replacement.zone.kicked-entry.v1` consumes that immutable fact for one closed
mandatory self-replacement. It creates a nested +1/+1 counter event and an
optional affected-object Flying, First Strike, Haste, or Trample grant, then
delegates commitment to the existing counter and entry-keyword owners. Partial
cards, unsupported costs, kicked triggers, spell riders, dynamic quantities,
and open entry effects fail closed without Oracle interpretation.

`ability.activated.self-zone-move.v1` owns the corrected activation origin and
closed movement descriptor for source-self hand returns and tapped
reanimation. `generic.self-zone-move.v1` lowers it to an intent pinned to the
stack source's physical and logical incarnation. Resolution revalidates the
origin and current Aura form, then delegates destination replacement, owner-
zone routing, tapped entry, attachment cleanup, normalized zone events,
projection, and replay to the canonical zone-transition owner. Battlefield
materialization uses the shared descriptor-driven complete-card admission
query; no handler-family check or runtime Oracle-text comparison participates.

The `effect.life` family accepts the compiler's fixed direct-player and
opponent relations without adding a second life owner. Targeted gain, loss,
and opponent drain revalidate the selected active player before resolution;
each-opponent drain derives the current opposing seats. All requested changes
enter the existing replacement-aware life batch so multiplier choices,
journaling, trigger records, rollback, projection, and replay retain their
canonical behavior.

`participation.untap-step.static.v1` lowers closed source, attached-object,
global, and other-player static wording into immutable CR 502 participation
values. The runtime query evaluates the complete typed `ObjectQuerySpec`
against current effective battlefield characteristics and current controller
and attachment relationships. A pure planner produces prohibited and
additional untap identities; `quorune/untap_step_coordination.py` commits them
through the canonical untap owner and holds triggers until upkeep. Maximum
untap limits have a typed fail-closed descriptor but are not compiler-promoted
as supported selection behavior. Optional, variable, qualified, phasing, and
selection variants remain residual. Runtime code does not inspect Oracle text
to decide untap participation.

`replacement.token.additional.v2` represents the closed mandatory fixed
additional-token family. Its descriptor carries an optional card-type and
subtype filter plus one immutable token definition. Inert `display_text` is
separate from typed keyword and activated-ability descriptors; current runtime
code never interprets that display string as Oracle authority. The replacement operation
updates the existing `token.create` event atomically, so newly added token
characteristics participate in the normal replacement rediscovery loop while
the same source cannot apply twice. The token owner commits every resulting
specification with one creation timestamp only after APNAP ordering completes.
The v1 handler remains registered solely for pinned reviewed semantic-pack
compatibility. Historical Game Record v3 token descriptors receive one
compatibility-only field migration without parsing or trust promotion. Optional
choices, quantity multipliers, state-derived token
definitions, and modified entry instructions remain unsupported.

`continuous.characteristics.fixed-public-state.v1` accepts both its historical
scalar condition payload and the additive typed object-query condition schema.
Source and attached-object queries are evaluated in the runtime domain against
effective layer-5 characteristics; the rules snapshot receives only their
boolean results. Fixed public object counts reuse the characteristic quantity
resolver and skip this layer-6/7c component while resolving that boundary, so a
type-changing effect can change the count without creating a characteristic
evaluation cycle. Direct attacking, blocking, tapped, untapped, enchanted,
equipped, and modified affected sets use the same `ObjectQuerySpec` permanent-
state predicate. Declaration restrictions consume the same snapshot for their
closed public conditions; they do not add a combat-local query registry. No
runtime Oracle parser or parallel applicability registry is involved.

`continuous.characteristics.public-state-keywords.v1` retains complete typed
keyword fragments behind the same public-state predicate and target relation.
Its keyword labels and fragments use the ordinary query-grant validator;
fixed generic ward and protection keep their established consumers. Conditional
combat and spell-cast keyword triggers use the current recipient's fragments.
The earlier characteristic-only descriptor continues to reject fragment fields.
An already created trigger retains its consequence when the live grant ends.

`modification.cast-cost.public-state.v1` wraps the existing public cast-cost
modifier with one typed live public-state predicate. The cast-cost collector
uses the shared condition evaluator before returning the ordinary modifier to
the total-cost owner. Offer and commit use that same collector, preserving
current ability presence, source control, generic floors and colored payments.
Fixed spell subjects also accept disjunctions of represented types and colors.
An unqualified first or second spell uses the caster's complete committed cast
history for the turn; a type-qualified ordinal counts only matching spell
types. A multicolor spell receives one modifier for a matching color union.
Ordinal predicates that require historical colors, subtypes or payment choices
remain unsupported by the card-type history schema.

`permission.cast.self-zone-public-state.v1` evaluates an explicit self-graveyard
permission against the same typed public-state facts. Outside the battlefield,
the condition's controller-relative facts use the card's owner through a shared
immutable source view. The ordinary casting proposal still owns timing, cost,
target and current-zone checks. The earlier controlled-subtype permission
descriptor retains its existing schema and behavior.

Controller-count conditions also accept a closed pair of canonical subtypes
joined by “or” or “and/or”, with one shared tapped or untapped qualifier. One
public query counts each matching permanent once, including a permanent with
both subtypes. Bare creature-subtype nouns include noncreature Kindred
permanents; they do not add a creature-type requirement. Current control and
phasing govern membership. Changing the condition revokes a later graveyard
cast offer. Casting checks the condition before mana abilities; payment retains
the legal cast's locked cost and ordinary resolution when a qualifying mana
source departs.

`permission.cast.self-public-zones.v1` adds only the card owner's explicitly
declared graveyard or face-up exile origins. The descriptor contains typed zone
data and requires complete CardProgram admission and the current selected-face
component. It retains ordinary timing, mana and additional costs, hand casting,
and resolution destinations. It supplies no Flashback exile replacement or
temporary designation. New incarnations use their own printed permission;
face-down cards and unrepresented condition, cost or restriction tails fail
closed. Existing conditional self-graveyard schemas retain their gates.

`permission.cast.self-graveyard-discard.v1` supplies a separate paid graveyard
method. Its sealed fixed count adds selected-hand discards to the ordinary
printed mana cost through the canonical additional-cost owner. Current owner,
source face, whole-program admission and ability-component presence gate that
method. Normal hand casting and an independent ordinary zone permission retain
their own costs. Revalidation binds the selected method to its normalized
discard descriptor before payment; replacements and atomic movement remain
with the existing zone-cost transaction. The permission is repeatable and
retains ordinary resolution destinations and timing.

The same quantity boundary supports versioned distinct basic land types,
graveyard card types, colors, maximum/total mana value and colored mana-symbol
counts. It reads current copy/type/color data only through layer 5. Exactly two
leaf quantities may be added without conflating them with a union; fixed
positive multipliers and nonnegative offsets remain explicit. Paired all-zone
power/toughness definitions retain independent field offsets. Legacy count and
definition payloads retain their existing codecs. Controller hand-size arithmetic
reads only the unfiltered zone cardinality. Source-counter values support fixed
positive multipliers and nonnegative offsets, including their composition.
All-graveyard definitions also count instant or sorcery cards through the public
query boundary. Hidden characteristic reads, power/toughness aggregates and
arithmetic outside the represented fixed forms remain unsupported.

`controller_opponent_count` is a versioned public-roster quantity for the
represented free-for-all profiles. It counts other players still in the game
without reading objects or hidden zones. Resolution amounts use the locked
controller and current roster at the instruction boundary; a departed player
does not remain in a count captured when the spell was announced. The same
quantity supports fixed arithmetic and independently exact result owners.

Public-state condition schema 6 composes two through four flat typed leaves
with `all` or `any`. The snapshot owner captures each leaf separately from the
same source and public state, preserving distinct query quantities and status
facts. Static consumers retain their layer-timing exclusions for every leaf.
Intervening-if triggers evaluate the whole condition at occurrence and again
at resolution. Legacy condition codecs remain unchanged; missing snapshots,
unknown leaves and unsupported nested or mixed conditions fail closed.

Devotion has a distinct versioned quantity reduction through `devotion.py`.
Under CR 700.5a it reads current mana costs after copy, control and text effects,
before later characteristic changes. One hybrid symbol contributes once to a
two-color union. Typed fixed static increases use the existing current ability
component map and cease after ability removal, phasing or source departure.
Characteristic definitions, resolution amounts, mana production and self cast-
cost reductions and closed devotion comparisons share this owner. Every newly
compiled consumer declares the devotion capability separately from its result
capability.

Compiled token quantity replacements participate only while their source's
current static component is present. The shared token discovery path reads
that component map before materializing a replacement. Legacy descriptors
retain their existing compatibility boundary; a current typed descriptor
requires an integer schema version. Counted token results enter the ordinary
replacement, simultaneous entry and state-based stabilization transaction.

`continuous.attached.fixed-characteristics.v1` keeps one reciprocal live
attachment relation while lowering its closed operations into layers 4, 5, 6,
7b, and 7c. It supports fixed or typed public-query power/toughness, base
power/toughness, type, subtype, color, and separately trusted keyword changes.
An Aura or Equipment line whose outer characteristic grammar is already closed,
or a source-controller, source-opponent, or global live battlefield query, may
also carry exactly one quoted activated, fixed-output mana, or triggered
ability. The compiler lowers that quote to a separately keyed exact CardProgram
and adds only its typed `GrantedActivatedAbilitySpec` or
`GrantedTriggeredAbilitySpec` in layer 6. Granted activations may preserve the
closed mana, tap, untap, source-sacrifice, life-payment, selected-object, and
represented restricted-mana costs already owned by the activation transaction.
Activation and trigger discovery bind the program to each affected recipient,
suppress it when the current grant is absent, and never execute the quote as
runtime text.
Dynamic quantities resolve through the shared cycle-safe layer-5 boundary; an
"other" attached-object quantity excludes the attached subject rather than the
Aura or Equipment source. Ability removal is ordered before additions from the
same effect, and the shared static-component query determines whether the
source still contributes any component. Exact attached restraints may carry a
typed declaration fragment and an all-ability or nonmana activation
prohibition; source-controller relations remain anchored to the granting
source.

Relative attachment counts use a versioned `attached_to_recipient` quantity.
The resolver follows the source's reciprocal attachment relation and counts
the recipient's current Equipment or Aura/Equipment union, including the
granting object when it matches. Each physical attachment counts once;
controller changes do not change membership. Reattachment, phasing and source
departure reuse the existing relation and layer-5 quantity boundaries.

Names, text changes, open target-relative state, multiple quotes, source
discard or exile, energy, loyalty, variable, dynamic, alternative-zone or
usage-limited activation costs, external attachment-source references, and
untrusted granted rules remain outside this owner.

`rule.cast.*.v1` components discover only current trusted battlefield sources
whose shared layer-6 static-component fragments still apply. One immutable
descriptor carries the player scope, public spell query, fixed condition,
origin-zone restriction, one-per-turn maximum, or chosen-name relation.
`rules/casting/proposal.py` consumes timing and prohibition results during both
offer and commit, using the canonical cast journal for supported all-spell and
noncreature limits. Attached-player limits use the reciprocal player-
attachment owner. `rule.stack.uncounterable.fixed-query.v1` is consulted by the
canonical stack-counter operation at application time, so source departure,
phasing, control, or ability removal changes the verdict without mutating the
stack object.

`restriction.activation.fixed-public.v1` uses that same current-component
boundary and the existing activation availability path. It compares current
public object predicates, typed mana and loyalty classifications, chosen names,
or reciprocal attachment relations; it never creates a second activation
catalog. The exact attached untap compound contributes an ordinary
`UntapStepParticipation` to the existing untap planner. Dynamic, temporary,
cost-changing, target-relative, granted, copied, and unrepresented compound
forms remain unavailable rather than being inferred from Oracle prose.

## Ownership boundaries

Components receive immutable source-authorized facts, never
`CommanderEngine`, mutable `GameState`, projection internals, or persistence
objects. They do not select by printed card name or Oracle ID. Each subsystem
owns its event model, ordering, replay continuation, validation, and final
commit:

- [damage](damage.md) and [prevention](prevention.md)
- [drawing](drawing.md)
- [counter placement](counter-placement.md)
- token creation through `quorune/token_creation.py`
- [continuous-effect decisions](../adr/0020-continuous-effect-duration-and-applicability.md)

The [extension guide](../extension/runtime-component.md) defines the contributor
workflow. Architectural rationale remains in
[ADR 0007](../adr/0007-cardprogram-runtime-components.md),
[ADR 0010](../adr/0010-replacement-event-tree-and-token-owner.md),
[ADR 0011](../adr/0011-counter-placement-event-and-mutation-owner.md), and
[ADR 0008](../adr/0008-runtime-trust-and-governance-hardening.md).

### Fixed attached otherwise stats

Complete fixed additive P/T alternatives on an enchanted creature use the
existing attached and public-condition owners. The compiler emits the
otherwise modifier as a source-bound base and adds the difference between the
two printed modifiers while the recipient condition holds. Both components
remain ordinary layer-7c modifications with one attachment and source lifetime.

The condition reads the enchanted creature's current color, types or combat
state, independently of the Aura and either controller. Recipient ability
removal does not remove its Aura effects. Source departure removes both stat
components, and resulting toughness follows ordinary state-based actions.
Base-setting, permission, variable and unrepresented otherwise branches stay
outside this fixed additive production.

### Qualified copy-entry choices

Intrinsic copy entry keeps its historical schema-1 selection contract. A
schema-2 descriptor adds closed public Equipment, nonland, fixed current-power
and entered-this-turn predicates through the same replacement owner. The
prospective entry controller supplies any relative controller relation. These
choices do not target, so Hexproof and Shroud do not remove eligible choices.

Eligibility reads current characteristics and the current incarnation's public
entry history. The selected copy then retains separate copiable values,
excluding temporary stat changes, counters, attachment state and unrelated
effects. Typed copied programs and entry replacements keep their existing
ownership. Cascade may put a permanent onto the battlefield in time to become
an eligible copy source, including during another player's turn. Hidden-zone,
unknown-exception and unrepresented recursive copy forms remain outside this
contract.

### Conditional queried declaration grants

`compiler/conditional_declaration_grants.py` composes an independently closed
public condition with a closed battlefield query and one recipient-relative
declaration fragment. Pure grants use the existing schema-6
`continuous.declaration.public-state.v1` descriptor, with no added keyword or
power/toughness modifier. The same module retains the existing mixed
characteristic-and-declaration compiler path, exported through its original
`declaration_nodes` entry point.

The static source's current controller supplies both the condition actor and
recipient query. Current matching entrants receive the grant; source departure,
ability loss, control changes, and query membership changes recompute it. The
existing layer-6 and combat declaration owners govern both blocker domains and
accepted commands. Source-relative opposing-controller rules and incomplete
conditions or sibling clauses remain residual.

### Temporary blocking capacity

Plain instructions allowing one referenced creature to block additional or
unlimited creatures this turn create `ResolvedBlockCapacityRule` values in the
existing duration journal. They retain the affected incarnation, accumulate
separate additional permissions and survive later ability removal. A departed
or returned incarnation cannot inherit a pending or resolved permission.
Direct creature targets use ordinary resolution revalidation; an optional fixed
power/toughness modifier remains a separate characteristic component. Cleanup
expires both components through the existing journal owner. Blocking capacity
does not waive evasion, tapped-state or other declaration restrictions.

### Conditional chosen characteristics

`continuous.characteristics.conditional-chosen-designation.v1` gates an existing
schema-1 chosen-characteristic descriptor with a typed current public condition.
It delegates both the retained designation and recipient query to
`ChosenCharacteristicsHandler`. The compiler may resolve a plural continuation
to one explicit, independently closed chosen query in the same complete static
paragraph. Its unconditional component remains separate from the conditional
component.

The ordinary public-state snapshot owner supplies the source's current
controller facts. Query recipients and retained per-incarnation choices remain
live through control changes; departure clears the choice, and re-entry asks
again. The shared static-condition validator rejects numeric characteristic
conditions that would need a separate layer-timing owner. Existing chosen-query
schema-1 descriptors retain their shape and behavior.

### Source chosen-color mana

`ability.activated.mana.source-chosen-color.v1` combines the shared closed mana
activation-cost descriptor with `ChosenColorManaOutput`. The output occupies the
existing tagged `dynamic_mana_output` field; fixed, color-set, and public-quantity
encodings retain their shapes. Literal amounts, an optional fixed alternative,
and existing spend restrictions form a closed output contract.

The mana runtime reads the source's retained public WUBRG designation. Explicit
activation, automatic payment, and land-produced-color discovery use the same
current modes. The activation owner captures the original source color before
all costs, so source or selected-source sacrifice cannot erase it. Public
quantity mana retains its post-object-cost, pre-source-departure snapshot. The
existing payment/usage transaction is delegated to
`rules/activation/payment_commit.py` without changing cost order or mutation
owners. Mana still resolves immediately for the current activator. Departure
clears the designation and re-entry obtains a fresh choice.

### Entry color exclusions and tapped paragraphs

`replacement.zone.entry-color-exclusion.v2` validates one excluded canonical
WUBRG color. It delegates to the existing intrinsic entry replacement producer,
which offers only the remaining values to the battlefield destination controller.
The plain schema-1 color and creature-type descriptors keep their encodings.
Both variants participate in duplicate same-kind choice scoping.

`compiler/entry_color_composition.py` recognizes complete unconditional
enters-tapped plus bound as-it-enters color paragraphs. It emits separate,
independently owned tap-state and designation descriptors with the full original
source span. The canonical immutable replacement journal orders and applies
them; the zone owner stores one choice and tapped state on the entering
incarnation. Unknown exclusions, ambiguous subjects, conditional prefixes and
unrepresented sibling clauses remain residual. Chosen-color mana and spending
restrictions continue through their existing shared output and payment owners.

### Fixed Spree mode costs

`SpreeModeCosts` binds one canonical literal mana vector to every printed mode of
an existing complete nonrepeating modal spell. The compiler retains the original
whole source span and independently closes each branch before emitting the
version-1 `spree` casting-cost schema. Ordinary modal encodings remain unchanged.

The casting cost owner expands finite distinct mode sets into correlated cost
options before total-cost modifiers. Each option carries the exact selected
mode target schema and adds those prices to its existing base, including a free
base. The shared affordability, target validation and payment owners enforce
that binding; resolution retains printed mode order and ordinary copy behavior.
Variable prices and incomplete mode bodies remain residual.

### Fixed Entwine mode costs

`EntwineModeCost` binds one fixed additional mana vector to every mode of a
complete choose-one spell. The version-1 `entwine` cost schema admits one
ordinary mode or all printed modes. Intermediate subsets, repeated modes,
variable or complex prices, and unresolved sibling modes remain residual.

The existing cost-option and target owners bind each selection before total
cost reductions and payment. Choosing all modes adds the Entwine price once,
including when a typed permission waives the base mana cost. Resolution uses
printed order, so later modes observe the results of earlier ones. The same
public mode and cost-option fields serve offers and accepted commands.

### Fixed mana Emerge alternatives

`FixedManaEmergeSpec` declares one ordinary mana alternative for a normal
creature card. The cost owner binds each option to a selected controlled
creature's current incarnation and mana value. The existing sacrifice-cost
owner retains selection, payment, owner-zone routing and committed events.

Emerge reduces generic mana in the total after cost increases; excess reduction
cannot pay colored or colorless obligations. A selected creature may tap for
mana before being sacrificed. Validation after mana payment requires that same
incarnation to remain available. Offers and submissions share the selected
option and existing sacrifice-card fields, including their ordinary aliases.
Copied characteristics supply current mana value. An ordinary cast retains its
printed cost, mana value and cast triggers without an Emerge sacrifice.
Complex prices, qualified sacrifices, other card forms and unresolved
Emerge-paid result bodies remain outside this family.

### Fixed mana Replicate and repeated additional payments

`FixedRepeatedManaCostSpec` and the common optional-cost expansion represent
source-pinned positive ordinary-mana costs and immutable paid counts. The
Multikicker and Replicate owners retain distinct descriptors, cost options and
count fields while sharing that arithmetic. Additional payments enter the
total before increases and reductions and remain optional with a free base.

`FixedManaReplicateSpec` binds one complete printed instance on a normal
instant or sorcery. A paid cast creates one trigger with the correlated count
and a locked spell template; an unpaid cast creates none. The existing spell
copy targeting owner validates the separate typed trigger, offers independent
new targets or retained targets, and creates copies through the ordinary copy
owner. Countering the original leaves its trigger intact. Copies revalidate
targets and do not count as casts or trigger Replicate again. Existing Storm
descriptors, copy choices and historical continuations retain their format.

Variable, nonmana, zero-priced, repeated or granted instances, permanent spells
and unrepresented sibling bodies remain residual.

### Fixed Cleave text variants

`FixedManaCleaveSpec` binds one fixed alternative price to independently closed
normal and bracket-removed bodies. Bracket derivation is compiler-only and
rejects malformed, nested or empty brackets. All body paragraphs participate;
an unknown sibling prevents the pair from closing.

Overload and Cleave share immutable body fingerprints, paired-program assembly,
leaf closure and price/program correlation through the closed
`SpellTextVariantKind` values. Their source productions and serialized spell
choices remain distinct. Cleave keeps each body's actual target schema, so
both can be targeted or both can have no targets. The selected program has an
empty mode list and its complete-source certificate remains mandatory.

The normal price retains bracketed restrictions. Paying Cleave selects the
derived program before target validation and rechecks it at commit. Copies
retain that program and its remaining restrictions. Existing target
revalidation, live quantity queries, private draws, total-cost modifiers and
zone owners execute the typed effects. A free normal base cannot select the
broader Cleave body. Unclosed bodies, complex or variable costs, repeated
instances and unsupported source forms remain residual.

### Fixed Overload text variants

`FixedManaOverloadSpec` binds one fixed alternative price to fingerprints of
the normal and overloaded spell bodies. The compiler derives the target-to-each
body and requires both forms to close through existing effect owners. It emits
a separate `spell_variant` program with no target schema. Both programs retain
the original Oracle identity, source spans and complete-source certificate.
Missing derived lowering keeps the whole card residual.

The existing `cost_option` selects Overload. The casting owner correlates the
price and complete generated program before validating targets, then checks
the same choice at commit. The overloaded program has no modes or targets;
live object sets are sampled by ordinary query/effect owners at resolution.
Hexproof does not stop the effect, while applicable damage protection remains
effective. Printed mana value is independent of the alternative price.

The canonical copy owner retains the selected semantic program without new
target or mode choices. Ordinary casting remains targeted. A separate free
normal base cannot waive the chosen Overload price. Base-zone alternatives
retain their existing order through `rules/casting/base_cost_options.py`;
additional costs and total-cost modifiers keep their existing owners.
Variable or complex prices, variable printed bases, unclosed bodies, repeated
instances and other source forms remain residual.

### Fixed instant and sorcery stack copies

`FixedStackSpellCopySpec` represents one copy of a current instant or sorcery
spell, with an optional current-controller restriction and explicit permission
to choose new targets. The compiler retains the original source span and
routes the same closed shape through spell, triggered, activated and granted
ability lowering. Existing target validation and the staged stack-copy choice
owner create the independent copy through `CopyStackItemIntent`.

The copy retains modes, X, casting choices and original target incarnations.
Unchanged targets can be retained after becoming illegal; newly selected
targets must be legal. A departed and returned object is a new target only
when explicitly selected as one. The copy producer still needs its own target
spell to remain legal at resolution. Copies are not casts and pay no new costs.

These programs declare a complete-source boundary. Current-source validation
checks the compiler's exact whole-card certificate, preventing an independently
recognized copy clause from executing beside an unclosed printed sibling.
The existing handler-based admission contract remains unchanged. Multiple
copies, extra qualifications, and type or color exceptions remain residual.

### Ordinary Conspire tap payments and copy triggers

`FixedConspireSpec` binds one printed Conspire instance to an optional pair of
creature tap costs. The current object query supplies untapped controlled
creatures; each must share a color with the current spell. The two creatures
may match different colors of a multicolored spell, and their summoning
sickness does not prevent this nonsymbol tap cost.

Each represented pair has an ordinary cost option bound to its current logical
incarnations. Existing affordability and mana-payment owners exclude those
creatures as mana sources. The casting owner revalidates the pair after mana
payment, then commits it through the existing tap-cost owner. A departed and
returned creature invalidates the prior option. A free base retains the
optional tap payment.

The paid fact creates one separately validated Conspire trigger using the
existing spell-copy template and target-choice owner. Countering the original
does not remove the trigger. Copies can retain or change targets and are not
casts; their effects use the ordinary runtime owners, including token copying.
Existing Storm prompts, logs and saved continuation shapes retain their format.
Multiple or granted instances, permanent spells, variable printed bases, mixed
consuming payment mechanics and unresolved sibling bodies remain residual.

### Fixed mana Disturb and source-local graveyard exile

`FixedManaDisturbSpec` binds one front-face declaration to its transforming
back face and fixed alternative price. Existing face and cost-option fields
carry the cast. Graveyard authorization, back-face selection, target offers
and submission validation share that authority. Total-cost modifiers observe
the back face's spell type. A generic free-cast permission cannot waive the
intrinsic Disturb price; independently authorized transformed-cast effects
retain their existing authority.

The resolving spell passes its chosen face to the ordinary permanent-entry
owner. Original mana value still derives from the front cost. Copiable
snapshots retain source Oracle and face provenance so ordinary physical and
token copies execute the copied face's programs while retaining their own
identity and mana value.

The separately printed graveyard-to-exile clause uses a source-local zone
replacement component, with the same current ability and face checks for
stack and battlefield subjects. It applies to copied and token subjects
without requiring a Disturb payment marker. Ability removal can suppress it;
bounce and other zone resets remove copied/current back-face state normally.
It participates in the ordinary replacement class, preserving affected-player
choice among competing replacements. Complex prices, repeated declarations,
other card forms and unrepresented sibling bodies remain residual.

### Typed token children in modal branches

`compiler/modal_token_branches.py` adapts the existing typed token-node compiler
for complete ordinary, Spree, and Entwine modal branches. Each branch retains its token
fragments and independently closed generated child nodes. Identical branch
bodies may share one immutable child definition; different source rows and
abilities retain distinct keys. Unknown children or sibling branches keep the
whole modal face residual.

`compiler/modal_spell_faces.py` assembles the parent spell and those child nodes
with the original spans and existing capability gates. Generated registration,
recording and loading preserve the child definitions. Token creation and later
abilities use the existing runtime owners, current token controller, normal
activation costs and timing. The creator spell can leave without removing a
token's ability. No runtime Oracle parsing or new token engine is introduced.

### Half quantities in player results

`HalfQuantitySpec` is a closed version-1 quantity with divisor two and explicit
up/down rounding. It clamps negative effect results to zero under CR 107.1b.
The compiler pairs it with existing represented player and selector shells.
The existing `lose_life` instruction reads a typed half amount at its current-life boundary and delegates
the complete batch to the canonical life-change owner.

Fractional `choose_cards_apnap` counts retain the existing instruction and zone
owners. Before collection starts, `selection/fractional_counts.py` seals each
player's candidate domain and required rounded count. Every issued and accepted
choice revalidates those values; zero domains skip automatically. The shared
APNAP contract validator is extracted without changing ordinary fixed counts.
No client count override or runtime Oracle parsing supplies the quantity.

### Half amounts on represented event-player results

`event_player_result_values.py` validates positive literal draw/life values and
independently typed half-life amounts. The compiler normalizes their/your only
after an existing compatible event subject has bound. Its subject/event matrix
and generated-program reference checks remain authoritative.

`rules/half_event_player_shapes.py` combines the half owner with the existing
event/trigger dependency proof. The emitted amount remains typed and resolves
through the existing life owner at instruction time. The damaged player's
identity remains sealed while that player's current life can change before
resolution. No object-controller reference is replaced with a damaged player,
and unsupported antecedents or amount fields remain residual.
