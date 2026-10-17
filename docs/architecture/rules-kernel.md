---
title: "Rules kernel"
status: "current"
authoritative_source: "quorune engine and rules modules, including quorune/control_history.py, quorune/day_night.py, quorune/permanent_transform.py, quorune/turn_history.py, quorune/echo.py, quorune/saga_progression.py, quorune/mentor.py, quorune/relative_power_target.py, and quorune/target_predicates.py"
verified: "2026-10-17"
audience: "rules and engine contributors"
maintenance: "hand-maintained"
---

# Rules kernel

Ordinary Morph, Megamorph and Disguise turn-up offers keep their canonical
unsuffixed action identity when acceptance reads the printed method from the
offer payload. Manifested permanents expose distinct suffixed method choices.
Both paths retain the same expiry and proposal-fingerprint validation before
payment, including special actions taken while Split Second is on the stack.

Normalized event discovery orders both live and explicitly captured source
lists by zone timestamp and stable object identity. Checkpoint hydration cannot
change pending trigger identities through dictionary insertion order. APNAP
placement still groups by controller, and each controller chooses the order of
their simultaneous triggers through the ordinary trigger-order capability.

Fixed mana-producing spells and ordinary entry or loyalty abilities resolve
through the existing stack and mana-pool owner. Mana production does not make
a spell, an entry trigger or a loyalty ability a mana ability. Additional
sacrifice and loyalty costs commit before resolution; a countered spell adds
no mana. Typed spending restrictions enter the canonical provenance lots and
use the same public spell-type predicates for advertised and accepted payment.

Fixed activated mana abilities may add their selected output and then apply one
typed source result. Damage is dealt to the activation controller through the
canonical damage transaction, so prevention changes damage while retaining
mana. A next-player untap result locks the activation controller and original
permanent, even after a control change. Both explicit activation and automatic
spell payment apply the result once after mana production. Result-bearing
activations are irreversible for mana undo and retain ordinary transactional
rollback, replacement continuations and replay.

Enlist adds an optional tap cost to an accepted attack declaration. Each
instance may tap one distinct eligible controlled nonattacking creature that
has haste or has been controlled since the turn began. All choices validate
before the canonical tap owner commits them. The linked reflexive trigger uses
the donor's power on resolution, with immediate last-known power after
departure. The original attacker incarnation receives the until-end-of-turn
power modifier; a blinked attacker does not. Negative additive results use
zero under CR 107.1b. Attack choices sort before history capture so saved
mapping order cannot change replay.

An intrinsic reveal-or-tapped entry replacement privately selects a matching
card in the prospective controller's hand or declines. Selection is validated
against the original hand-card incarnation before mutation. The existing hand
inspection mutation owner records only that selected card as public knowledge
and publishes its reveal; unchosen candidates remain private. The replacement
journal preserves the decision through save/load and replay. Decline sets tapped
entry, while acceptance leaves other tap-state replacements in force.

Exert adds a separately selected optional attack cost. Current typed abilities
and trusted linked result identities govern both the offer and accepted
declaration. The canonical tap-state owner records the exerting player and
publishes the linked occurrence while attack costs are paid; the resulting
trigger waits for the existing APNAP placement boundary. Its controller and
source incarnation remain locked after a control change or departure. Exert
does not require a tapped permanent. Repeated exertions expire together during
the exerting player's next physical untap step, regardless of the permanent's
current controller. That step does not consume a stun counter on the prohibited
permanent. Departure removes the old duration through the canonical zone reset.
Unsupported conditional permissions, standalone activated Exert costs, and
unrepresented phasing or skipped-step compositions remain outside this family.

Self-transform activations pin the original source incarnation and its
transform count when placed on the stack. The existing transform owner keeps
the same permanent, counters, damage, attachments and acquisition history.
A controller change preserves the original activation controller and source
binding; a blink creates a new object and invalidates that activation. Current
fixed keywords on a double-faced spell or permanent come from trusted printed
programs for its selected face. The parent record's keyword union cannot keep
front-face Defender on a transformed Flying creature.

An original transforming back face retains the mana value calculated from its
front face while retaining its own current mana cost and colors. Copiable
snapshots derive the copy's mana value from the copied face's own mana cost;
they do not copy the original's derived front-face value. A copy of a
transforming back face therefore has mana value zero. Modal faces and split
card stack and prospective views retain their separate selected-face rules.

Prototype selection changes base mana cost, color, power and toughness while
casting, on the stack and on the battlefield. It preserves the card's types
and independently compiled abilities. Both normal and Prototype choices use
the shared legality and payment owners, including available alternative costs.
Copy snapshots retain the selected characteristics. Leaving the stack for a
zone other than the battlefield, or leaving the battlefield, removes the
selection; a blinked card returns with its ordinary printed characteristics.

Multiple blocking consumes current typed additive or unlimited capacity at
declaration. Ordinary single-attacker responses remain valid; extra pairings
use a distinct attacker list for the blocker. The existing declaration solver
checks per-pair evasion, defending player, costs and maximum requirements.
Accepted pairs commit through the canonical combat relationship owner. Later
loss of the permission does not undo those blocks. The graph owns all pairs;
the legacy `blocking` field remains one representative blocking identity.
Trigger occurrences, relative combat targets and damage snapshots consume the
complete graph, and a blocker assigns its power once across its recipients.
Validated pairs sort by attacker and blocker identity before commit. Client
mapping order therefore cannot change the authoritative blocker list or saved
command replay.

Fixed selected-object tap costs may accompany a source tap symbol. The source
and selected objects are distinct: the source cannot pay both costs. The source
tap obeys creature acquisition timing; selected-object taps without a tap
symbol may use newly controlled creatures. The canonical tap-cost owner pays
the complete group before discovering tap events. Offers and commands share
the same source-excluding predicate and reject incomplete, duplicate, stale,
or ineligible selections atomically. Untap-symbol and mixed zone-change cost
combinations retain their existing unsupported boundaries.

## Responsibility

The kernel validates and applies deterministic game transitions: priority,
turn structure, zones, costs, choices, stack resolution, combat, state-based
actions, represented continuous/replacement effects, and semantic programs.
It is authoritative for legality and never delegates rules decisions to a UI or
pilot.

## State and mutations

`GameState` owns players, cards, zones, stack, turn/combat state, pending
decisions, events, yields, and fidelity telemetry. During migration,
`CommanderEngine` remains the declared general mutation owner. Casting and
activation use read-only immutable proposal builders followed by declared
typed commit owners; `mana_activation.py`, `tap_state.py`, and
`token_creation.py`, `regeneration.py`, `destruction.py`, `permanent_exile.py`, and
`return_to_hand.py` own focused transactions behind typed host protocols.
`stack_counter.py` owns represented counterability checks, stack removal,
replacement-aware countered-spell movement, telemetry, and public journaling.
Destruction delegates shield removal to the counter owner and permanent
movement to the zone owner. Regeneration shields are public logical-object
state: the regeneration owner creates them, cleanup and zone-object reset clear
them, and destruction consumes one while coordinating canonical tap, damage,
and combat state. State-based execution commits a replacement-only result even
when no permanent changes zones. Regeneration, represented damage clearing and
Umbra protection participate in the changed-state decision and the same typed
destruction commit; an Indestructible no-op leaves the stabilization loop
unchanged. Source or recipient zone movement is not a prerequisite for those
replacement mutations. Direct exile and return snapshot owner, controller,
and object identity through one closed single-object transition substrate before
delegating their distinct requested destinations to that same replacement-aware
zone owner.
`public_zone_moves.py` extends that substrate with an immutable public-origin
set selected by typed owner/controller relation and APNAP order. It commits the
complete set through `ZoneTransitionOwner.move_cards_simultaneously`; it does
not add another zone engine or mutate one object before every applicable
replacement has been prepared. Empty affected sets are ordinary no-ops.
`commander_zones.py` supplies CR 903.9's physical-designation boundary: owners
may replace hand or library movement with the command zone, and each new public
graveyard or exile incarnation receives one state-based command-zone choice.
A decline is remembered only for that logical incarnation. Melded/merged CR
903.9c cases and mixed-owner/controller replacement ordering outside the typed
boundary remain explicit limitations.
Represented single-target, fixed-set, lethal-damage, and Deathtouch destruction
all snapshot the current canonical effective-keyword view before committing
through `destruction.py`. The fine-grained
`permanent.indestructible.ordinary` capability covers ordinary intrinsic,
temporary, continuous-grant, and keyword-counter instances already represented
by that view. It prohibits only destruction: zero toughness, sacrifice, exile,
and other nondestruction movement remain owned by their normal rules paths.
Ordinary Trample still assigns lethal damage without treating Indestructible as
damage already assigned. Exact self, named-source, direct-target, and
attached-creature regeneration instructions create the same public shield for
represented effect or damage state-based destruction. An exact direct-target
or fixed-set cannot-be-regenerated rider suppresses only that regeneration
disposition; Indestructible and effect-destruction shield counters retain their
ordinary outcomes. Static, variable, qualified, damage-linked, and ordinary
competing shield-counter choice grammar remains fail closed. Qualified or
conditional player-facing Indestructible and unrepresented copy, face-down,
merged-object, or ability-changing producers
also remain blockers; both aggregate mechanics remain partial.
Capability lifecycle and replay hydration have narrowly declared compatibility
ownership. All other rules helpers return values or operate through an
approved mutation boundary. Typed semantic handlers receive
an immutable rules query and emit intents; they cannot import the engine or
state model. The intent executor calls existing canonical engine methods or
the focused tap-state port.
`saga_progression.py` owns the immutable ordinary Saga precombat snapshot and
chapter-dispatch sequence while `counter_state.py` owns the simultaneous lore
write. Entry lore remains inside the zone-replacement transaction; the later
turn-based action deliberately bypasses effect-qualified counter replacement.
Typed direct-target destruction, permanent-exile, battlefield
return-to-owner-hand, own-graveyard card return, and stack-counter handlers
likewise commit only through their focused transactions. Battlefield and
graveyard return share one origin-pinned, replacement-aware single-object
transition substrate while retaining distinct compiler shapes, handlers,
capabilities, and journals. Their expanded compiler predicates reuse the
shared direct-permanent target schema or the existing owner-scoped graveyard
card schema; optional, sequence, and homogeneous forms compose those leaves
without another runtime owner. The aggregate mechanics remain untrusted where
dynamic regeneration, replacement ordering, mass selection, linked exile,
opponent-graveyard recursion, reanimation, conditional payment, alternate
counter destinations, or other
unsupported grammar and interactions are materially reachable.
The fixed public-zone-move handlers are a separate broad grammar over the same
owners: one handler revalidates a public graveyard-card target, while the set
handler emits one typed simultaneous intent.

Intrinsic copy entry shares the canonical replacement and zone owners. An
enters-copy replacement captures layer-one values and activates the selected
source's typed intrinsic entry producers. Commit retains the physical card's
Oracle identity and carries separate copied program provenance into effective
characteristics, static owners, activation catalogs and event discovery.
Departure LKI retains that provenance for already-triggered abilities; the
new zone incarnation discards the old copy. Copied Aura attachment uses the
existing affected-controller choice before entry. Its retained replacement
result preserves the chosen values while the later attachment choice resumes.
Spell resolution and record trust use the ordinary record owner until copied
program provenance changes the physical card's effective identity; only that
changed identity requires lookup of the originally announced physical card.

Schema-v2 public set descriptors
also carry the exact prospective battlefield controller and tapped policy for
closed graveyard-entry groups; the canonical zone owner evaluates entry
replacement facts before committing one timestamped batch. Fixed owner-
destination clauses reuse those existing effect operations for target,
source-incarnation, current-or-last-known attachment, controller-selection,
and APNAP each-player selection forms. One adjacent actor-private hand-choice
handler revalidates a stable ordered set before submitting the ordinary single-
object move, while the fixed public tap-state handler delegates its locked set
to the existing tap/untap and stun-counter owners. The canonical zone owner
derives the hand, graveyard, or library container from physical ownership and
applies Commander and other destination replacements before committing a new
logical incarnation. No handler receives mutable state, parses Oracle prose,
or bypasses target, attachment, commander, replacement, trigger, projection,
or replay coordination.

Continuous characteristics are a shared rules responsibility rather than a
client reconstruction. `continuous_effect_state.py` owns the authoritative
resolution-effect journal and expiration; `characteristic_evaluation.py`
combines that journal with live CardProgram static effects for both engine
legality and principal-scoped projection. Raw journal entries and physical
object identities never enter the projection. Resolution-created combat
declaration rules share the duration journal but are not characteristics and
do not enter the layer evaluator. The declaration owner combines them with
restrictions from current effective static abilities through one read-only
query, so ability removal affects static restrictions without erasing an
independent rule that already resolved.

## Inputs and outputs

- Inputs: a pinned `GameState`, semantic registry, server-issued action ID,
  capability-scoped choices, and deterministic randomness already represented
  in state/commands.
- Outputs: an accepted transition and events, or a typed rejection with the
  original state preserved.

## Dependencies and invariants

The rules domain may depend on model and rules helpers. It must not depend on
HTTP, WebSockets, server persistence, AI providers, or browser code. A rejected
command is transactional. Legal alternatives are currently payable, hidden
information is projected separately, and state stabilization precedes the next
priority decision.

`turn_step_owner.py` retains the physical active-player identity when that
player leaves during their multiplayer turn, as required by CR 800.4j. Later
steps still occur and priority moves to an active seat, but the departed player
does not perform turn-based actions: in particular, draw-step entry records an
explicit skipped draw instead of submitting an inactive affected player to the
strict draw and replacement owners.

`turn_priority_owner.py` clears the successive-pass round when the priority
holder confirms a concession. The existing player-departure owner transfers
priority to the next remaining player. That player receives a fresh usable
window over the changed game, including when turn order wraps to a player who
passed before the concession. Unconfirmed and unauthorized concessions remain
transactional rejections.

## Casting, activation, and action offers

`rules/action_catalog.py` composes executable offers from the same pure casting
and activation queries used during command validation. Each offer contains a
canonical proposal fingerprint and an expiry revision. Execution accepts the
offer only while its source, cost, target, timing, and payability facts remain
equivalent, then commits through `rules/casting/commit.py` or
`rules/activation/commit.py`. Stale offers fail before mutation.
For a modal double-faced card, offer construction and command validation derive
the same spell-program key from the selected front face name. A typed land-face
program cannot authorize an unsupported spell face, and a trusted spell target
schema cannot disappear from its advertised offer.

`abilities.py` generically lowers represented colon abilities, the supported
Craft reminder grammar, and one typed activation-usage limit per
printed Exhaust ability. `activation_usage.py` is the single owner for the
usage journal: the use persists across turns, control changes, and phasing for
the same object, and the ordinary zone-change reset clears it for the new
object. Offer and commit share the same typed verdict; a commit postcondition
fails the whole transaction if the limit was not consumed. Usage-limited mana
abilities require explicit activation and are excluded from reversible
tap-mana and automatic-payment paths. Effects that permit another Exhaust use
remain unsupported. CardPrograms may grant an activated ability through a
serialized descriptor; historical card-named markers are interpreted only by
the Game Record v3 compatibility adapter.

The compiler now attaches the complete closed activated-ability value to the
source-pinned CardProgram through `activation.catalog.pinned.v1`. Runtime
discovery consumes that catalog, rule-derived basic-land-type abilities, typed
layer-6 grants, and typed token characteristics; it does not reinterpret Oracle
prose. Copy effects preserve the catalog, ability-removal effects remove it,
and a text-changing effect clears descriptors compiled from the replaced text.
Intrinsic basic-land mana abilities materialize from the current land types
after layer 4 within characteristic evaluation. Later ability removal can
remove them while the land keeps those types, and later layer-6 grants remain
distinct. Offers, direct activation, and automatic payment consume the surviving
typed abilities. Ending a removal restores the intrinsic abilities on the next
evaluation without changing the land's types.
Reusing a characteristic state rebuilds only intrinsic descriptors previously
created by that evaluator, before applying ability removal again. This private
provenance stays out of snapshots; caller-supplied reserved intrinsic IDs still
reject rather than masquerading as derived abilities.
The catalog unifies discovery for ordinary Crew, Cycling, fixed Typecycling,
fixed counter-keyword activations, and represented mana abilities without
replacing those families' specialized execution owners or capability closure.
Fixed Typecycling is limited to Basic land, one basic land subtype, artifact
land, Wizard, and Sliver queries with ordinary fixed mana costs. It shares the
source-discard activation owner with Cycling and the actor-private restrictive
search owner with other semantic searches. Two through four complete printed
Typecycling siblings on one Oracle line retain separate source spans, program
keys, costs, queries, and activation offers. Each descriptor uses its line's
legacy `abN` identity plus a bounded `:typecycling-instance:N` suffix; ordinary
single-ability descriptors retain their existing identity and codec. A malformed
sibling prevents exact admission of the line. Dual-type queries, variable, nonmana,
granted, and ability-presence queries such as Affinitycycling remain
fail-closed. Current games cannot use the isolated Game Record v3 compatibility
parser. Discard-self and exile-self activation costs use the same destination-
replacement continuation as casting costs: a competing choice rolls the
complete priority action back, keeps the continuation private to the affected
activator, and resumes the exact source-pinned action before stack placement.
Exactly one fixed positive named-counter removal from the current battlefield
source is also part of the pinned catalog. Offer and commit read the same
public counter state, pin the source incarnation, and pay through the canonical
exact counter-removal transaction before stack placement or immediate mana
production. Variable, all, chosen-kind, repeated, another-object,
nonbattlefield, and granted forms remain fail closed.

Fixed complex activation mana costs remain inside that same catalog and action
path. One typed option records the selected colored-hybrid or two-brid vector,
the selected Phyrexian life alternative, and any required snow-mana count.
Mana production records a compact provenance lot only when the source is Snow
or the mana has a spending restriction; a unit carrying both facts stays in
one lot. Offer and commit therefore agree on current mana, life, Snow-source,
and restriction payability, and a stale option rejects before source tap, mana
spend, life payment, or stack placement. The lot journal is authoritative
checkpoint state but is not projected as mana-source identity.

Ordinary printed `Crew N` is compiled once into a source-spanned activated-
ability descriptor. `crew.py` owns the immutable current-characteristic
candidate set and aggregate-power cost plan; activation offers and commits use
that same typed owner. The plan permits creatures with summoning sickness
because Crew does not use their own tap-symbol abilities, excludes the source
even if it is already a creature, counts the signed power of every selected
creature, revalidates each physical and logical identity before tapping, and
permits an empty selection for Crew 0. Resolution adds the Artifact and
Creature types without changing
supertypes and while retaining the source's existing card types and subtypes,
as required for an "artifact creature" result, and binds that layer-4 effect
to the source incarnation that created the stack object. A Vehicle that leaves
and returns is not affected.
Crew prohibitions, alternative costs, becomes-crewed triggers, granted or
copied Crew, and effects that crew without activating the ability remain
explicitly unsupported.

Mandatory fixed nonmana casting costs use source-spanned typed descriptors.
The counter-placement and single-object zone-change families share the ordinary
cast proposal and commit boundary, but retain distinct mutation owners. The
zone-change descriptor closes its operation, origin, destination, choice field,
and immutable object predicate for one discard, sacrifice, exile, or
return-to-owner-hand payment. Hand and graveyard choices are owned private
objects; battlefield choices are phased-in controlled permanents. Offer and
commit use the same current-characteristic query. Commit delegates the physical
move to the replacement-aware zone owner before stack placement, then dispatches
normalized discard, graveyard-departure, or battlefield-departure facts before
the cast event. Historical unversioned discard and sacrifice schemas remain in
an isolated compatibility query outside `CommanderEngine`. Unsupported cost
grammar residualizes the entire spell rather than exposing a cost-free result.

Ordinary printed Convoke is a face-pinned typed cast-cost descriptor rather
than live keyword interpretation. `convoke.py` owns the immutable candidate,
contribution, whole-vector payment plan, and deterministic fingerprint;
`rules/casting/costs.py` owns the shared offer and submission query. The plan is
computed after represented total-cost reductions and before mana payment. A
selected creature is excluded from the mana-source plan, then revalidated for
current physical/logical identity, controller, Creature type, color, tap state,
and phasing before the canonical mana and tap owners mutate state. Hybrid,
Phyrexian, snow, broader cost ordering or restriction, payment replacement,
granted or removed Convoke, and rules-text equivalents fail closed.

Source-pinned self and battlefield static spell-cost modifiers enter that same
total-cost stage. The selected face or current layer-6 source supplies one
strict typed metric over public effective objects, source counters, party,
color and effective-name counts, mana value, devotion, Domain, life, or the
bounded discard, sacrifice, attack, spell, and Commander-cast histories. The
metric preserves whether opponent thresholds are existential per opponent or
aggregate across all opponents. The casting owner applies increases before its
generic or colored reductions and before payment mechanics.
Offer construction and accepted-command validation call the identical query;
a control, object, or turn-fact change therefore reprices or rejects the stale
action before mutation. Target-dependent prices, open characteristic amounts,
and unjournaled history remain unsupported. This bounded pipeline does not
claim complete CR 601.2f or CR 601.2h payment ordering.

## Extension and event participation

Reusable mechanics belong in focused rules modules and typed semantic
operations. The current tap-state owner commits only the represented single
permanent and all-effective-creature operations; it preserves stun replacement
and phased-out behavior without claiming the complete replacement or layer
systems. The token owner runs represented token events through an immutable
replacement batch before committing one timestamped batch and dispatching
enter events. The closed mandatory fixed additional-token family transforms
that same event with a typed operation, extends its type/subtype subject for
replacement rediscovery, and cannot apply one source twice.
Fixed Investigate, Afterlife, named and predefined definitions, exact targeted
copies, and next-end-step creation all converge on that same transaction.
Token Changeling and declaration restrictions are serialized as typed
characteristic fragments, while Powerstone, Junk, and Vibranium use closed
ability profiles; token display text never becomes runtime authority.
The Blood profile requires one generic mana, tapping and sacrificing its source,
and discarding one selected owned hand card. Shared activation availability and
cost validation reject missing hand cards, tapped sources and unpaid costs.
The complete payment precedes an independent private draw on the stack; token
departure does not remove that queued result.
Lander delegates its fixed basic-land search and shuffle to the ordinary private
search owner. Mutagen delegates its fixed counter to the counter transaction
owner and shares ordinary target-incarnation revalidation. Their finite builtin
result keys carry no variable effect grammar; the explicit token profiles own
their mana, source tap and sacrifice, and Mutagen's sorcery timing.
For each committed token instruction, the owner first publishes the complete
group's identity/controller/type history, then seals the final entry-time
characteristics used by entry predicates. History-dependent static power,
toughness, and keywords therefore see the completed event. Separately
instructed post-entry grants still occur after discovery, and sequential
creation instructions remain distinct. This does not alter prospective
replacement preparation or departure-time last-known information.
`replacement_decisions.py` persists competing
affected-seat choices as ordinary Game Record v3 continuations, and represented
zone-destination changes use the same exact selection journal before mutation.
`state_based_execution.permanent_sba_snapshots` captures one current public
characteristic, controller, attachment and timestamp view for the pure
state-based evaluator. The engine's compatibility facade delegates that query.
Role grouping keeps the unique latest Role for each controller and attachment;
older Roles receive ordinary nondestruction graveyard actions. Missing facts or
an unrepresented timestamp tie fail closed.
Role token creation retains the canonical token owner. The compiler pins
Enchant and child programs into the token characteristics; a typed choice
previews replacements before choosing relative timestamps. The commit owner
rechecks the attachment incarnation and exact replacement preview, reserves
ordered timestamps without reordering identity allocation, and attaches each
Aura before entry events. Same-controller Role uniqueness follows those
explicit timestamps; different controllers retain their separate Roles.



Triggers consume normalized events; replacements transform represented events
before final mutation; state-based actions run to a fixed point. Mandatory
direct counters now use a closed typed stack-target grammar and an exact
intrinsic counter-prohibition declaration. A physical spell countered through
that owner emits one pre-counter normalized occurrence, followed by a
card-graveyard occurrence only when the committed destination is the
graveyard. Countered abilities, spell copies, conditional-payment counters,
intrinsic alternate destinations, broader prohibitions, and universal draw,
damage, prevention, and entry participation remain blocked. New rules work must
identify event/replacement participation and use capability IDs from the
versioned registry.

Every committed move of a physical nontoken card into a graveyard from another
zone also emits `card.enter_graveyard`. This after-transition occurrence seals
the destination characteristics, current logical incarnation and graveyard
owner, and discovers surviving observers through the current ability query.
It represents "from anywhere" subscriptions independently from predeparture
death or leaves-battlefield LKI, including during simultaneous deaths. A
replacement to another destination emits no graveyard-entry occurrence.
The historical countered-spell `card.graveyard` event remains distinct.

The simultaneous zone-transition owner separates history publication from
trigger discovery. After every replacement-aware move in a batch commits, it
records entry, death, discard, and sacrifice history for the actual final
occurrences, then discovers each occurrence's triggers. Intervening conditions
therefore see the complete simultaneous event regardless of enumeration order,
while genuinely sequential moves remain separate. Trigger discovery still uses
the existing pre-event source, characteristic, controller, and attachment LKI;
a redirected or prevented move contributes only the history of the transition
that actually occurred.

The normalized trigger vocabulary also carries a bounded public binding
closure without adding producer-specific engines. Attack, block, damage,
Cycling, step, and zone-transition owners emit immutable occurrences; trigger
predicates compare those facts with the source's current reciprocal attachment
or the departing object's sealed attachment, owner, prior-controller, type,
color, and power facts. The exact one-or-more creature-card graveyard departure
form deduplicates within one simultaneous trigger batch. All of these programs
use the same current layer-6 ability-component query and ordinary APNAP
placement; unsupported aggregate, linked, ambiguous, or dynamic forms remain
untrusted before mutation. Bounded controller-attack, discard, sacrifice,
combat-damage, token-entry, and owner-relative graveyard-departure predicates
also consume those same occurrences. Their one-or-more variants deduplicate
only inside the producer's canonical simultaneous batch, and public damage or
zone predicates read sealed committed or last-known facts rather than live
Oracle text.

Named-counter zone predicates consume version-two occurrence counter maps.
The zone owner captures departure counters before any reset, including for all
members of a simultaneous move, and captures entry counters after committed
entry replacement and initialization. The trigger owner compares only the
positive counter names from the appropriate snapshot. It rejects missing facts
for this predicate, preserves known-empty facts, and never reads counters from
a returned or post-departure incarnation. Version-one occurrence payloads keep
their original representation and do not invent counter information.

Two-event triggered abilities remain one CardProgram ability and one effect
body. `rules/event_subscriptions.py` owns the immutable ordered subscription set;
the existing dispatcher selects the matching normalized event and condition
while CardProgram projection exposes both trigger entries. Every supported
subscription shares one battlefield active zone, current-ability
applicability, APNAP placement, projection, and replay boundary.

`turn_history.py` retains current-turn events plus one bounded per-player
spell-count summary for the immediately previous turn. `day_night.py` consumes
the previous active player's count during the second untap-step action, before
the ordinary untap plan, and holds resulting triggers for the upkeep APNAP
batch. Applicable paired Daybound/Nightbound components establish the unique
public designation and synchronize their represented faces immediately;
night entry selects the back face before entry characteristics are finalized.
`permanent_transform.py` is the single active-face mutation owner. It preserves
the logical object, controller, counters, damage, attachments, and timestamp,
increments a replayed transform count, and ignores an activated or triggered
instruction whose captured count is stale. Source reincarnation, ability
removal, save/load, and public projection all use those typed identities rather
than Oracle prose.

Committed represented casts dispatch one strict immutable `SpellCastEvent`
that pins the physical spell, logical incarnation, controller, origin, stack
reference, and canonical current card types. Ordinary Prowess is lowered once
to a source-spanned trigger program plus a typed ability fragment. Trigger
discovery requires that fragment in the source's current layer-6
characteristics, then delegates APNAP batching to the shared trigger owner and
the identity-pinned +1/+1-until-end-of-turn result to the continuous-effect
owner. Removing abilities suppresses future triggers but does not erase a
trigger already on the stack. Rules-text equivalents, qualified variants,
unsupported grants or copies, and trigger multiplication remain fail closed.

Ordinary printed Storm is also a typed stack-zone spell-cast trigger rather
than a generic keyword-coverage switch. It snapshots the count of other spells
already cast that turn before the current cast is recorded, shares the normal
APNAP cast-trigger batch, and delegates target reassignment plus copy-object
commit to `selection/storm.py`. Each copy retains represented modes and X,
revalidates only its own changed targets, resolves as an ordinary counterable
spell copy, and does not emit another cast event. The source spell may leave
without erasing the locked trigger. Unsupported grants, text changes,
Gravestorm, face-down propagation, and unrepresented copied choices remain
outside trust.

Ordinary printed fixed-mana Echo uses one source-spanned trigger descriptor.
`control_history.py` owns the public acquisition timestamp and per-player upkeep
boundary used by its intervening condition; ordinary summoning-sickness turn
counts remain part of that same control-acquisition write. That owner records
a new acquisition only when the authoritative controller changes. A
new control instruction naming the same controller leaves combat relationships,
battlefield ordering and acquisition history intact, while still superseding
an older temporary restoration where applicable. Trigger discovery
freezes the ability controller, logical source identity, and acquisition fact,
then the shared payment choice emits only typed mana-payment or controlled-source
sacrifice intents. The additive control-history version is explicit in new Game
Record v3 checkpoints and manifests. Historical records without that version do
not acquire new hashed timestamps during replay. Nonordinary costs, cost and
restricted-mana interactions outside the current payment owner, granted or
copied Echo, and broader control-history mechanics remain fail closed.

For represented CR 611 object modifications, resolution-created effects lock
the affected physical/logical object set after successful preparation. Static
effects keep a live source-bound `ObjectQuerySpec` and recompute membership
after earlier layers. Unsupported duration or operation families fail before
the journal mutates. The closed zone-object keyword result lowers once from a
source-spanned CardProgram descriptor to a typed semantic intent and immutable
layer-6 journal entry. It survives cleanup, control change, and source
departure, but applies only while the affected permanent retains the same
battlefield logical identity. The runtime does not parse Oracle text or execute
display rules text to reconstruct that duration.

Closed fixed source-counter sequences use the same owners. The compiler emits
`$source.zone_object`; resolution requires the current battlefield source and
its stack-pinned logical identity before constructing the counter intent. A
counter replacement may suspend, but continuation validation rejects source
reentry before either the counter or later continuous result commits. The
later result is still owned by `continuous_effect_state.py`, so a failed commit
rolls back the resumed counter transaction rather than leaving a partial
sequence.

Combat declaration relationships commit through
`combat_relationship_state.py`. After a complete declaration, the engine
adapts public combat facts into immutable canonical attack or block transition
values. Typed transition derivation owns ordinary printed Exalted, Battle Cry,
Melee, Mentor, Flanking, and positive-integer Bushido and Rampage occurrences; the shared trigger
batch owns APNAP placement, and the continuous-effect journal owns their
identity-pinned layer 7c results. Mentor instead emits a typed targeted counter
result: `relative_power_target.py` owns current and predeparture source-power
snapshots, `target_predicates.py` owns the shared target-predicate evaluation,
and the counter transaction owns the write. Transition models have no mutable
state or engine dependency. Their narrow adapters may read effective
characteristics and delegate commits to the declared combat, trigger,
continuous-effect, target, and counter owners. Conditional or prose-equivalent
variants, unsupported granted or copied fragments, trigger multiplication, and
broader attack/block transition triggers remain explicit residuals.

Rampage uses the same sealed becomes-blocked transition and retains each typed
instance independently. Its rules helper counts the original attacker's current
blockers beyond the first only when that occurrence resolves. The existing
continuous-effect journal freezes the resulting bonus through end of turn;
later blocker departures do not change it. A departed or returned source
incarnation receives no modifier. Blocking with Rampage does not trigger it.

`ObjectQuerySpec` is a strict immutable predicate shared by those live effects
and other represented rules families. Its current schema distinguishes
all-required from any-required card types and preserves colors, subtypes,
supertypes, keywords, token/tap/phasing state, public relations, visibility,
source exclusion, excluded controllers, minimum color cardinality, and closed
public permanent-state predicates such as a named counter minimum. Historical
schema-v1 through schema-v6 payloads retain their original field sets so Game
Record v3 replay does not silently rewrite old descriptors.

Version-seven queries carry a fixed typed numeric characteristic predicate for
represented battlefield creature selection. They use the same comparison
value as direct targets and require exact effective stats. Live layer matching
and quantity expressions reject this field until their own read timing is
represented; adding it to a query does not implicitly grant those consumers.

The public battlefield compiler uses that same query for live layer-6 and
layer-7c applicability, public-state-gated targets, resolution-time set locks,
typed queried ability and declaration-fragment grants, and untap participation.
One descriptor may add fixed keywords and declaration fragments over the same
query without duplicating applicability. Positive ability presence is evaluated
after generic same-layer additions and removals of the required ability.
Resolution-time characteristic selection rejects ability-qualified sets.
Its explicit numeric creature filter reads completed effective power/toughness
before creating the new effect and locks the selected incarnations. Live
static quantities retain the separate layer-5 boundary.

Schema-five public conditions have a separate numeric creature-existence
predicate. Intervening triggers and resolution instructions read complete
current effective power or toughness under their locked controller. They
recheck intervening conditions at occurrence and resolution, include counters
and represented buffs, and reject unavailable numeric facts. Static
characteristic conditions reject this schema because their cycle-safe read
boundary remains at layer five.

Fixed public static conditions snapshot the same layer-5 query boundary along
with canonical aggregate hand/draw counts, typed current-turn spell-cast
history, named source and player counters, and the public monarch designation.
The snapshot contains no hidden object identity and is recomputed under the
source's current controller. Query counts may use authoritative tap,
attachment, counter, and modified state, but never later-layer ability presence
or dynamic power/toughness.

Combat declaration restrictions consume that same snapshot for fixed public
conditions rather than maintaining a combat-local condition registry. Direct
source-stat comparisons read current effective power or toughness at the
declaration boundary. Attached source-controller restrictions retain their
originating source, while all-ability and nonmana activation prohibitions on
the affected object remain ordinary current layer-6 fragments. See
[ADR 0099](../adr/0099-typed-public-declaration-conditions.md).

## Visibility and replay

The kernel holds authoritative information but never builds network responses.
Every accepted strategic command is recorded and must replay to the exact state
hash with the same rules, cards, and semantics fingerprints.

## Unsupported cases and evidence

Unsupported grammar or behavior fails closed through semantic/preflight or
runtime fidelity gates. The generated
[rules status](../RULES_COMPLETENESS_STATUS.md) is the authority for remaining
families. Primary evidence is the deterministic test suite, replay tests,
privacy tests, mutation/rollback evidence, and source-pinned conformance
artifacts.
