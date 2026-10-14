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
`previous_controller`. Aggregate attack events do not supply a unique defender. Program
promotion checks the event against each context reference. One or two complete
fixed leaves preserve printed order and require an explicit subject per clause;
`you` retains the resolving trigger controller. Optional, variable and linked
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
fixed modifiers. Creature subtype replacement removes only creature subtypes,
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

`permission.cast.self-zone-public-state.v1` evaluates an explicit self-graveyard
permission against the same typed public-state facts. Outside the battlefield,
the condition's controller-relative facts use the card's owner through a shared
immutable source view. The ordinary casting proposal still owns timing, cost,
target and current-zone checks. The earlier controlled-subtype permission
descriptor retains its existing schema and behavior.

The same quantity boundary supports versioned distinct basic land types,
graveyard card types, colors, maximum/total mana value and colored mana-symbol
counts. It reads current copy/type/color data only through layer 5. Exactly two
leaf quantities may be added without conflating them with a union; fixed
positive multipliers and nonnegative offsets remain explicit. Paired all-zone
power/toughness definitions retain independent field offsets. Legacy count and
definition payloads retain their existing codecs. Hidden characteristic reads,
power/toughness aggregates and open or nested
arithmetic remain unsupported rather than using an incorrect boundary.

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
cost reductions share this owner. Every newly compiled consumer declares the
devotion capability separately from its result capability.

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
