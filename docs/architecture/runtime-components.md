---
title: "CardProgram runtime components"
status: "current"
authoritative_source: "quorune/semantic_runtime component registries and schemas/card-program-v2.schema.json"
verified: "2026-08-15"
audience: "rules, compiler, runtime, replay, and extension contributors"
maintenance: "hand-maintained"
---

# CardProgram runtime components

Runtime components represent CardProgram behavior that participates outside a
single immediate resolution instruction. Typical families discover immutable
replacement effects, prevention effects, draw policies, or continuous effects
from live source descriptors. Components participate in a subsystem-owned
transaction; they do not commit state.

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

## Descriptor and registry contract

Every family declares a stable handler ID, schema version, event or layer,
rule references, capability dependencies, a strict descriptor validator, and
a deterministic inventory entry. Unknown registered fields, malformed values,
duplicate ownership, and unknown capabilities fail closed. The aggregate
registry provides discovery and a fingerprint only; family modules own
validation and lowering.

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

`modification.cast-cost.self-public.v1` represents fixed reductions printed on
the spell carrying the descriptor. Selected-face discovery evaluates only
public controller/opponent object queries, fixed public thresholds, total mana
value, devotion, Domain, and canonical spell, death, or active-turn facts. The
typed query distinguishes existential `an opponent` thresholds, evaluated per
opponent, from aggregate `your opponents` quantities. The result is a generic
or colored reduction vector applied by the same total-cost query used for
offers and accepted commands. Target-relative prices,
unrecorded history, dynamic power/toughness, chosen or hidden facts, and open
arithmetic fail closed rather than becoming a runtime prose query.

`ability.activated.mana.color-set.v1` binds a compiler-pinned relative object
query to the activating seat and reads only matching public battlefield
objects or that seat's graveyard. It derives colors from current effective
characteristics, feeds manual offers and automatic payment through the same
mode set, and treats an empty qualifying set as a legal activation that adds no
mana. Runtime code does not parse Oracle prose. Wider dynamic or conditional
mana wording remains residual.

`permission.action.static` owns immutable controller-scoped action
permissions discovered from current-face, trusted descriptors on phased-in
battlefield sources. Its closed handlers authorize either an owned graveyard
land through the ordinary land-play owner or controlled-creature activation
through the existing as-though-haste availability owner. Discovery itself is
read-only; ordinary timing, quota, cost, control, zone-transition, and commit
rules remain authoritative in their existing owners. Unrepresented wording
fails closed rather than falling back to current Oracle text.

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
remain residual. Level-gated Class abilities remain residual until their
applicability has a typed owner. Every trusted battlefield
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
state predicate. No runtime Oracle parser or parallel applicability registry is
involved.

`continuous.attached.fixed-characteristics.v1` keeps one reciprocal live
attachment relation while lowering its closed operations into layers 4, 5, 6,
7b, and 7c. It supports fixed or typed public-query power/toughness, base
power/toughness, type, subtype, color, and separately trusted keyword changes.
An Aura or Equipment line whose outer characteristic grammar is already closed
may also carry exactly one quoted activated, fixed-output mana, or triggered
ability. The compiler lowers that quote to a separately keyed exact
CardProgram and adds only its typed `GrantedActivatedAbilitySpec` or
`GrantedTriggeredAbilitySpec` in layer 6. Activation and trigger discovery bind
the program to the attached recipient, suppress it when the current grant is
absent, and never execute the quote as runtime text.
Dynamic quantities resolve through the shared cycle-safe layer-5 boundary; an
"other" attached-object quantity excludes the attached subject rather than the
Aura or Equipment source. Ability removal is ordered before additions from the
same effect, and the shared static-component query determines whether the
source still contributes any component. Conditions, names, text changes,
declaration restrictions, target-relative state, multiple quotes, unsupported
activation costs, external attachment-source references, and untrusted granted
rules remain outside this owner.

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
