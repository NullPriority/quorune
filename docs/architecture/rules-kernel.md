---
title: "Rules kernel"
status: "current"
authoritative_source: "quorune engine and rules modules, including quorune/control_history.py, quorune/day_night.py, quorune/permanent_transform.py, quorune/turn_history.py, quorune/echo.py, quorune/saga_progression.py, quorune/mentor.py, quorune/relative_power_target.py, and quorune/target_predicates.py"
verified: "2026-10-17"
audience: "rules and engine contributors"
maintenance: "hand-maintained"
---

# Rules kernel

Fuse is a hand-only casting method selected through the ordinary cast offer and
proposal. It creates one spell with both halves' names, types, colors, and
combined printed mana cost and mana value. Casting a single half retains that
half's stack characteristics. Fuse is compatible with an independently supplied
alternative mana cost or permission to cast without paying the mana cost; the
shared cost owner applies increases and reductions once to the combined spell.
It grants no permission to cast from another zone.

`rules/casting/characteristic_methods.py` shares prospective characteristics and
committed stack context for the represented characteristic-selecting methods.
`rules/fuse.py` requires a current complete declaration and its correlated
executable fused program, together with both current half programs. Standalone
half casts share this complete-source requirement. The existing resolution owner executes the left half
before the right in one resolution, including choices that suspend that
resolution, with no priority or state-based actions between halves. Ordinary
target revalidation and incarnation checks remain authoritative. Spell copies
retain the combined characteristics and target roles through the existing copy
owner. The canonical zone reset removes the original spell's Fuse annotation
when it leaves the stack.

`opening_hand.py` owns optional battlefield-entry actions after all multiplayer
mulligans and bottoming finish. It snapshots eligible final hand incarnations
and lets the starting player act in any order before proceeding through the
remaining seats. Each private choice selects one card or finishes that seat;
unselected hands stay hidden. The canonical zone owner performs each entry
without casting, paying mana or placing a spell on the stack. Entry replacement
and Aura choices suspend the same typed frame. Pending trigger batches remain
held until ordinary first-turn priority. An entry prevented in its original
hand does not consume that source's later opening action. The existing turn
owner starts the first turn exactly once after the opening actions finish.

Opening reveals share that ordered phase and require complete-card admission.
Each eligible final-hand incarnation may be revealed once without casting or
moving it. The canonical hand-visibility owner retains the reveal until the
first turn starts; learned identity remains known afterward. A separate
battlefield action on the same card is still available. Its versioned frame
retains both action identities and restores pending visibility after load.
`opening_reveal.py` creates one ordinary delayed trigger for the revealing
controller. First-global-upkeep, first-own-upkeep and first-own-main conditions
remain distinct, copies resolve separately, and source departure does not
cancel an already created delay. All results, including mana, use the ordinary
stack and their existing mutation owners.

Conditional self-timing evaluates current public facts for the would-be caster
and selected face before locking costs. Losing the qualifying permanent while
paying mana or other costs does not undo that legal cast. Actual conditional
Flash contributes through layer six on the battlefield and stack; “as though”
permission contributes no keyword. Cost modifiers inspect a detached proposed
spell under its caster, while completed cast events use its current after-cost
characteristics. Face-down methods suppress intrinsic timing and keywords.

Printed self-cast restrictions use the same selected-face proposal as advertised
actions and accepted commands. A prior spell belongs to the actual caster and
need not resolve; an opponent's spell does not satisfy that condition. A current
Snow-land condition uses control and current characteristics rather than
ownership. These conditions are checked before payment. Losing a qualifying
permanent while paying mana does not revoke the legal cast, and resolution does
not recheck a casting restriction. Face-down alternative characteristics suppress
the printed restriction. This boundary retains the existing mana-plan grammar:
choice-bearing costs outside the represented automatic forms require explicit
activation and do not gain a new payment route from a casting condition.

Fixed nonmana Kicker uses the existing optional additional-cost branch and
canonical life, selected zone-change and tap payment owners. The complete
source descriptor and its fingerprint are revalidated before mutation; its
typed payment leaves remain separate from mandatory prices and the base mana
cost. Distinct hand and battlefield prices may compose without reusing a cost
object. Subtype tap prices use a version-two creature predicate while version
one retains its wire shape. A selected tap without a tap symbol permits a
newly controlled creature and still reserves it against mana or other tap
payments. The committed kicked fact follows the existing stack and entry
history. Countering, losing targets or replacing a cost object's destination
does not refund the payment; unrelated reentry clears the old fact.

Kicker-dependent target groups use the same bound source schema in the offer,
cost option and accepted proposal. The unpaid group has no selected target;
the paid group retains its required count and separate target domain. Commit
rechecks that schema against the current sealed Kicker choice. The version-ten
cast-fact instruction selects its symbolic child program before ordinary value
binding, so inactive roles are never read. Existing target snapshots and
mutation owners handle partial target loss, new incarnations and printed
effect order. A copied spell retains its original role counts and restrictions
while allowing the usual new-target choice without another Kicker payment.

Self-cost reductions use the selected spell face and current caster's public
facts or committed cast history. The total cost locks before mana activation,
so sacrificing a qualifying permanent while paying does not revoke its
discount. Generic and colored components reduce only their corresponding cost
requirements, with a zero floor. A face-down casting method suppresses printed
self-reduction abilities while retaining independently applicable external
modifiers through the existing cost owner.

Counted self-price turn facts use an explicit versioned counted form for
creature deaths, opponents' spell casts and the caster's actual draws. Death
and cast counts consume the authoritative current-turn journal; draws consume
the existing draw transaction's counter. Reveals, Scry, bounce and the current
cast are not substitute events. A countered spell remains cast, and source
Flashback uses the same reduction before its complete cost locks. Generic
reductions floor at zero and preserve colored costs and result amounts.
Legacy truth-valued turn facts and draw thresholds keep their encoded meaning;
historical records with disabled journals retain that compatibility mode.

A represented self-price scalar counts all current counters on in-game players
and unphased battlefield permanents through their canonical counter snapshots.
Each individual counter contributes, including the compatibility poison and
energy fields and other named player counters. Ownership and control do not
restrict the total. Counters on cards outside the battlefield do not contribute.
The generic discount floors at zero and retains colored requirements. A
counter-bearing mana source sacrificed during payment does not change the
already locked price.

Intrinsic creature-count discounts also accept a closed fixed power or
toughness threshold over creatures the caster currently controls. The shared
public query reads effective characteristics, so setting effects, modifiers and
counters can change qualification. Opposing creatures and phased-out permanents
do not contribute. The generic discount changes the payment after X is chosen;
the spell's chosen X and any resulting target-controller payment stay intact,
including when the generic casting payment reaches zero.

Undaunted uses the same total-cost stage with the casting player's current
in-game opponents. Eliminated players do not contribute, repeated instances
accumulate, and a later elimination does not change an already locked cost.
The generic reduction leaves colored and nonmana payment obligations intact.

Fixed untap participation reads current full characteristics and counter state
before the simultaneous untap action. A subtype-only subject also matches a
current noncreature permanent with that subtype. An additional self untap uses
the current controller and occurs on each other player's untap step; duplicate
permissions cause one untap attempt. An own-step-only prohibition does not
block that additional untap. General prohibitions and stun replacement still
apply, and resulting triggers wait for upkeep and ordinary APNAP placement.

Ordinary Morph, Megamorph and Disguise turn-up offers keep their canonical
unsuffixed action identity when acceptance reads the printed method from the
offer payload. Manifested permanents expose distinct suffixed method choices.
Both paths retain the same expiry and proposal-fingerprint validation before
payment, including special actions taken while Split Second is on the stack.

Printed fixed nonmana Morph prices share that same action and whole-source
admission. Typed costs query current hand colors or controlled subtypes and
pay through the life, selected-hand-reveal, or simultaneous zone-cost owners.
Resource offers use the shared reference-array choice descriptors with exact
cardinality and principal-scoped candidates, including their ordinary UI forms.
Revealing publishes only the selected eligible card. Discards and returns use
their canonical response fields and retain replacement continuations across
save/load. Returning the source itself ends the action without inventing a
turn-face-up event for a departed incarnation. Compound, sacrifice, variable,
copied, and granted nonmana Morph costs remain outside this family.

Intrinsic entry applicability uses an isolated view of the selected face before
the zone change commits. The ordinary layer engine still removes abilities
when a type-setting effect applies. Current registries require their indexed
entry component; the narrow historical v3 entry adapter retains its earlier
unindexed execution contract during replay.

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

Automatic mana payment also accepts a represented fixed-output ability whose
only nonmana cost is sacrificing its source without tapping it. Discovery and
commit share current source eligibility and ability-condition checks. Such a
source may be tapped or newly controlled; tap-symbol abilities retain their
ordinary restrictions. The source can contribute once and its sacrifice uses
the canonical zone-cost owner. Additional costs and choices outside this
bounded form require explicit activation.
Mana abilities with a separate mana payment also require explicit activation.
The source/output-only automatic plan cannot pay that price. Discovery and
plan commitment exclude the same paid mode while retaining any free mode on
the same permanent. Explicit activation still pays the complete price through
the normal proposal and payment owners before producing mana.

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

Fixed external entry-tapped replacements use the same immutable zone-entry
snapshot and commit owner. A current battlefield source selects a fixed union
of permanent card types and a global, controller, or opponent scope. The
prospective entrant's types and destination controller determine applicability;
the source's current effective ability determines whether the rule exists.
General sources affect later entries, including tokens, and do not affect their
own entry or other permanents entering alongside them. Competing tapped and
untapped replacements use the affected controller's private ordering choice.
Qualified unions, source-state conditions, and cast-specific or copy-entry tap
instructions remain separate contracts.

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

Fixed untap selection limits compose as cumulative bounds on current scoped
sets. The active controller chooses one inclusion-maximal mandatory set before
simultaneous untapping; overlapping scopes can permit maximal sets of different
sizes. Optional sources may be omitted. Unaffected mandatory permanents untap
automatically. Static prohibitions, next-step prohibitions, and exert exclude
subjects before selection, while selected stun replacements consume a slot
and remove one counter. The existing tap-state owner commits each attempt.
No priority is granted during the choice, and held triggers wait for upkeep.
Server and browser forms share subset bounds and mandatory completion metadata.

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

Ordinary Banding declares optional disjoint groups of current attacking
incarnations through `rules/banding.py`. Every group attacks the same player,
planeswalker, or battle and contains at least one creature with Banding and at
most one without it. The attack form maps each member to a Banding member that
leads its group. The canonical combat relationship owner stores these groups
and removes departed combatants; losing Banding later preserves membership.
Blocker anchors obey the ordinary declaration restrictions, requirements,
capacity, and costs. After a legal declaration, that blocker blocks every
current member of the anchor's band, including members it could not separately
be declared to block. Public snapshots and blocker offers include these groups.
Creatures retain their own abilities. Quality-based “bands with other” and
unrepresented effects that add blockers or make creatures blocked remain
residual. The [damage owner](damage.md) explains Banding's assignment control.
General blocking triggers occur once per blocking creature. Conditions tied
to the individual blocked creature retain a separate occurrence per matching
relationship; trigger multipliers still multiply each applicable occurrence.

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
An acquired intrinsic copy ability applies before entry. The snapshot captures
the reachable copiable forms without mutating the entering object. Each form
enables its own entry producers; unchosen forms contribute no results. Candidate
alternatives share one replacement occurrence, so acceptance or decline consumes
that ability's opportunity across later forms under CR 614.5. Distinct acquired
abilities remain available, and only the final form enters and emits entry events.
Fixed additive copy exceptions retain other types while adding represented
card types or creature subtypes. These exceptions are copiable values and
survive later ability removal; unknown words and unrepresented grants reject.
Version-three copy selectors also choose public creature cards in graveyards.
Version-three exception values can retain the entering card's full name or
printed/prototype stats and replace its card types with artifact while retaining
only applicable artifact subtypes. These values are resolved before entry and
remain copiable. Fixed P/T replacement removes the copied P/T CDA; additive type
exceptions retain applicable CDAs. Actual zone return clears the old copy.
Creature-copy entry exceptions can also add a quoted activated or triggered
ability or a represented keyword when its independently compiled child closes
over trusted capabilities. Fixed mana and Crew grants retain their typed costs;
Dethrone and Myriad retain executable fragments/programs, not just keywords.
The shared promotion owner recognizes the exact source-return shape through its
existing capability matcher. Added fragments remain copiable, and repeated
grants retain their multiplicity. Static, recursive, and unbound-value grants
remain outside this entry-copy grammar.
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

Soulbond relationships use `soulbond_model.py` and the narrow
`soulbond_state.py` mutation owner. Optional entry choices validate both
unpaired, controlled creature incarnations without targeting them. Control,
phasing and zone boundaries end the relationship immediately; characteristic
observers expire pairs that cease being creatures. Returning to the earlier
state does not restore an expired pair. Losing Soulbond alone leaves an
established pair intact; paired effects still follow current source-ability
presence. Typed granted abilities belong to their recipients, and pending
triggers retain their locked controller and last-known source information.

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

`rules/block_prohibitions.py` owns the shared read-only block-prohibition
verdict. It evaluates ordinary typed restrictions in their existing order, then
source-specific blocker pairs. Those pair records lock both incarnations in
the same duration journal, remain independent of abilities and controllers,
and expire at cleanup. They do not remove a block that was already declared.

Resolution-created casting prohibitions also share that duration journal
without entering characteristic evaluation. Their typed entries retain the
resolving controller and optional targeted seat independently of source
movement. `rules/temporary_cast_restrictions.py` supplies the active player
rules to the shared proposed-spell legality query, which evaluates the actual
proposed spell characteristics before offers or payment. The canonical journal
owner expires them at cleanup and restores them through the ordinary save/load
codec; no runtime Oracle text or source-presence check supplies their meaning.

Target-set characteristic instructions retain strict compiled validation.
After an independently owned quantity is evaluated, the resolved P/T delta may
be zero. The resolution owner still validates the complete instruction and
selected incarnations, then records a typed layer-7c zero modifier when no
keyword component exists. Zero values preserve ordinary target legality,
duration, privacy and replay; they do not invalidate an otherwise legal spell.

Fixed opponent-creature keyword prohibitions use the same layer-6 owner but
remain distinct from ordinary keyword removal. A prohibition suppresses that
keyword regardless of grant timestamp, including keyword counters, while
leaving other characteristics intact. Static and counter grants resume when
the prohibition ends. A resolving grant records immutable target-incarnation
exclusions for keywords it cannot add; ending the prohibition later does not
create the denied grant. Same-effect type changes and source ability removal
are evaluated in a detached proposal before those exclusions are committed.

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

The priority owner also restores priority after casting, activation, mana
activation, land entry, and other special actions have stabilized. Paying a
player's full remaining life is legal; if that payment eliminates the actor,
priority goes to the next surviving player in turn order with an empty pass
round (CR 118.3b and 800.4a). A surviving actor retains the current priority
epoch so reversible mana activations remain in the same window. Pending
stabilization choices and game completion prevent priority restoration.

When every remaining player loses simultaneously, final departure removes the
last player's owned objects without requesting ordinary zone replacement or
APNAP choices: outside the game is not a zone (CR 104.4a and 400.11). The
existing replacement-aware removal path remains in use while any player
survives. Public knowledge and previously hidden identity retain their existing
boundaries, and the replacement snapshot model still requires a nonempty APNAP
order for ordinary transitions.

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
Craft reminder grammar, and typed activation-usage limits for
printed Exhaust and ordinary once-only abilities. `activation_usage.py` is the single owner for the
usage journal: the use persists across turns, control changes, and phasing for
the same object, and the ordinary zone-change reset clears it for the new
object. Offer and commit share the same typed verdict; a commit postcondition
fails the whole transaction if the limit was not consumed. Usage-limited mana
abilities require explicit activation and are excluded from reversible
tap-mana and automatic-payment paths. Effects that permit another Exhaust use
remain unsupported. CardPrograms may grant an activated ability through a
serialized descriptor; historical card-named markers are interpreted only by
the Game Record v3 compatibility adapter.

Conjunctive activation instructions share the same current timing, predicate
and usage verdict at offer and commit. Conditions apply before payment;
sacrificing a qualifying land, exiling graveyard cards or removing the source
as a cost does not make a paid ability recheck those activation instructions
at resolution. A rejected command never consumes the allowance. Ordinary
once-per-object state is separate from Exhaust and once-per-turn state.
`rules/activation/turn_event_conditions.py` reads canonical current-turn cast
and life-loss events. A countered spell was still cast; an ordinary copy was
not. Non-damage life loss qualifies, while life gain, own loss and prevented
damage do not. Missing or stale history fails closed. Descriptor-owned runtime
requirements independently require the new usage and history capabilities.

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

Fixed additive basic land types use a typed layer-4 component with global,
controller-land, or current attached-land scope. One basic type or every basic
type adds its subtypes while preserving printed abilities, other types,
supertypes, and names. A source's later ability removal does not undo an already
applied type effect; removing that source or its attachment ends the effect.
Type setting and its printed-ability loss use a separate contract.
Fixed basic-land-type setting replaces land subtypes and removes printed and
copiable abilities in layer 4. Other card types, nonland subtypes, supertypes,
names, colors, and stats remain unless another instruction changes them. Later
ability grants stay separate, and intrinsic mana derives from the new types
before layer-6 removal. Negative supertype queries distinguish nonbasic lands
from lands with a basic subtype. Prospective entry applies this same ability
presence boundary before printed entry replacements and triggers are collected.
Fixed global setters may select one current basic land subtype. Their
applicability uses the characteristics produced by earlier type effects. A
pure per-object dependency pass orders a type setter before a basic-subtype
predicate whose applicability it changes, respecting relation and locked-object
identity. Existing explicit dependencies and cycle timestamp fallback remain
authoritative; the pass does not create a general Oracle dependency parser.

The bounded enchanted-permanent conversion sets the affected object to a
colorless Forest land. It replaces old card types and subtypes in layer 4,
applies the existing basic-type printed-ability rule, and removes colors in
layer 5. Name, control, ownership and supertypes remain. Later ability grants
still apply. An affected Aura becomes unattached while remaining a land;
restoring its Aura type lets the ordinary attachment state-based rule act.
Source removal restores the prior characteristics without a gameplay cache.

Unconditional static land animation uses the existing fixed public setting
component. A complete fixed P/T sentence may select all lands or one current
basic land subtype, add Creature while retaining prior types and abilities,
and set a fixed base P/T in layer 7b. An explicit color word applies in layer 5;
otherwise the effect preserves colors. Membership remains live for entering
lands and subtype changes. Counters and ordinary modifiers apply after the
base setting. Existing control history governs attack and tap-symbol legality,
and existing World-rule timestamps remain authoritative.

Fixed settings declare their earliest source-participation layer from the
validated modifier. Type-producing settings participate from layer 4, color
settings from layer 5, and later-only settings retain the conservative layer-6
boundary. The existing component registry owns that descriptor query; no
second layer or component registry is introduced. Conditional animation,
chosen or variable P/T, unsupported subtypes and unowned result siblings
remain outside this production.
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
production. Version 1 retains one fixed counter name. Variable, all, repeated,
nonbattlefield and granted forms remain fail closed in this source-local owner.

Activated-cost parsing can split one complete fixed source-counter removal
followed by an unambiguous “and sacrifice it” conjunction after validating the
counter clause through the existing owner. The original cost text is retained.
Both typed costs keep ordinary availability, exact payment and same-source
identity; source departure precedes resolution and never refunds a countered
ability. Other antecedents, variable counts and unparsed tails remain residual.

A separate fixed selected-counter cost removes one +1/+1 counter from one
creature the activating player currently controls. The form, offer and payment
share the same public candidate query, and payment pins the selected incarnation
through the canonical exact removal owner. The source itself can pay this
counter while also paying its distinct tap resource; a tapped or foreign-owned
controlled creature can pay without being sacrificed. The paid object is not
a target, and later state-based death or illegal targets never refund it.
Other counter kinds, amounts, selectable payment groups and granted forms
remain residual until their own contracts are represented.

Version-2 source-counter costs represent exactly one or two counters of any
present kinds. The source-scoped form lists every legal distribution, including
two different counter kinds, with human-readable labels and incarnation-bound
choice IDs. Commit resolves that same current distribution and removes its
exact total through the existing owner. Removing negative, keyword or stun
counters updates ordinary characteristics without inventing an untap. Hybrid
mana and sorcery timing keep their own authority. The version-1 wire remains
unchanged; variable amounts, all counters and other payment objects are outside
this source-only grammar.

Source color-protection choices reuse the same resolution choice and the
existing version-7 temporary ability bundle. Each of the five color branches
contains one typed ProtectionSpec fragment for the exact source incarnation.
The locked ability controller chooses after costs; control changes preserve
that actor, and blink cannot transfer the grant. Ordinary targeting, damage,
blocking, Aura/Equipment state-based checks and cleanup consume the existing
protection and layer-six owners. The source ordinary-keyword reader continues
to own its unversioned leaves; versioned protection bundles have their own
complete minimum-dependency validation. Mixed qualities and unsupported
sibling costs remain residual.

Fixed single-color source protection emits the same typed bundle directly.
Ordinary mana or server-random discard activation costs precede the stack
result. The grant adds protection while retaining other printed abilities,
follows the original zone object across control changes, and does not follow
a blinked incarnation. Existing protection, Aura stabilization and cleanup
owners consume the result without introducing a resolution choice.

Random own-graveyard returns reuse public object-query facts and the canonical
simultaneous zone-transition port. Their immutable intent retains the resolving
controller, closed eligibility predicate, count and stack instruction identity.
The keyed selector belongs to the rules layer; semantic handlers only validate
and lower the instruction. All replacement choices finish before movement, so
reload or rejected choices cannot select a different subset. Returned public
cards remain known in hand, while unrelated hidden cards retain their projection.

Fixed source keyword choices compose the existing scalar option choice with
the source-incarnation characteristic owner. The ability controller chooses
one printed ordinary keyword during resolution, even if control of its source
has changed. Existing sacrifice, other-creature tap, counter and mana costs
remain paid. Departure or blink cannot transfer the effect to another
incarnation. An optional fixed P/T prefix runs once before the choice;
state-based actions wait until resolution completes. Both components expire
through the existing duration owner. Grammar admission, normal registration
and minimum runtime dependencies validate the same complete branch set.
Mixed protection choices, variable quantities and other choosers remain residual.

Ordinary two-arm activation prices use that same immutable option model when
both printed arms have the same source-tap resource. The compiler retains each
complete mana vector and the original cost text. Offer and commit apply current
generic increases to each arm and pay only the selected arm, with one source
tap. The runtime dependency reader validates option descriptors and requires
their existing mana-cost authority. Distinct nonmana resources, variable prices
and independently unsupported results remain residual.

Fixed complex activation mana costs remain inside that same catalog and action
path. One typed option records the selected colored-hybrid or two-brid vector,
the selected Phyrexian life alternative, and any required snow-mana count.
Mana production records a compact provenance lot only when the source is Snow
or the mana has a spending restriction; a unit carrying both facts stays in
one lot. Offer and commit therefore agree on current mana, life, Snow-source,
and restriction payability, and a stale option rejects before source tap, mana
spend, life payment, or stack placement. The lot journal is authoritative
checkpoint state but is not projected as mana-source identity.

Ordinary announced activation X uses that catalog and the same proposal and
payment path. `activation_x.py` validates the optional symbol count;
`rules/activation/x_costs.py` prices each symbol at the explicit nonnegative
integer chosen by the authenticated activator, before existing generic
reductions. Source-tap costs reserve the source from its own mana payment.
Offers advertise the numeric choice, and acceptance rechecks the chosen price.
The canonical `StackItem.x_value` retains each activation's independent value
through source departure, save/load and replay; existing result owners consume
it. Non-X descriptors and records preserve the field's historical absence.
The typed target-cardinality owner also binds homogeneous exact or up-to X
targets before shared target legality, retaining the same X on the stack.
Text-defined or restricted X, unrepresented target roles and nonordinary or
nonmana X costs remain outside this boundary. Activated-ability copying has
no represented owner and gains no execution path here.

Static generic activation-cost modifiers use the existing cost-component
registry. `rules/activation/cost_modifiers.py` reads only current trusted
sources, faces, controllers, attachments and public source types.
`rules/activation/pricing.py` shares their prices across availability, offers,
proposals and pre-payment validation. The chosen X enters the base price
before taxes and reductions. Minimum-one restrictions belong to each effect;
colored, colorless and snow obligations count toward the minimum, and an
already-zero cost stays zero. Distinct legal reduction-order prices remain
selectable. The paid total locks before payment changes its sources.

Fixed controller modifiers can select Equip or Cycling abilities. Equip uses
the existing built-in Equip identity. Cycling and Typecycling compare the
selected activation against a trusted registered Cycling descriptor, with its
exact bound semantic key; a similar discard-and-draw activation is not enough.
The selector adds no ability fields and does not inspect live Oracle text.
Generic reduction may reach zero while colored, colorless, discard and other
nonmana obligations remain. Other controllers and unrelated activations keep
their original prices, and current source loss revalidates an offered discount
before any cost is paid. Target-sensitive discounts remain outside this flat
selector grammar.

Fixed alternatives retain their life and snow obligations. Power-up supplies
its existing current-entry reduction facts to the same ordered price model,
and Waterbend reservations use the reduced generic amount before selecting
tapped objects. Automatic source/output plans accept only a currently zero
mana price; an explicit activation that pays mana cannot create reversible
mana undo. Unknown modifier qualifiers, variable or colored static reductions,
and unrepresented nonmana adjustments remain residual.

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

Mandatory announced-X life, owned-graveyard exile and owned-hand discard costs retain one declared
nonnegative integer across the cost option, proposal, payment and spell result.
The life query bounds X by current life. The zone query requires exactly X
distinct qualifying owned incarnations in the declared zone, excluding the spell itself;
its version-three descriptor carries `count_parameter: "x"` rather than a fixed
count. Existing fixed descriptors retain their serialized shape. A zero payment
needs no objects and is legal. The browser's dependent payment field follows
the declared X, while acceptance and commit revalidate the same typed query.
Reducing the mana price does not change X. Casting without paying the mana cost
forces X to zero when the mana cost contains X; an additional X on a fixed-mana
spell remains chosen and payable. Completed additional payments are not refunded
when the spell is countered. Optional, random, mixed selected groups, alternative
variable prices, and combinations with Convoke, Delve or Improvise remain
outside this announced-X cost family.
Hand discard admits unrestricted, creature and land cards through the existing
private selected-card query. It shares X with the original mana and target
contracts, pays every selected card before resolution, and retains that price
after countering or partial target departure. Earlier destination choices stay
in the ordinary payment journal when a later discard suspends the transaction.

One mandatory random hand-card spell price uses a separate closed descriptor.
The shared offer and acceptance query requires another hand card and exposes
no selected-card input. The existing `rules/casting/additional_zone_costs.py`
owner handles selection for both ordinary selected prices and this automatic
price. After mana payment, the server's stable payment identity selects the
random card, excluding the spell itself. That selection is passed
to the ordinary discard-cost commit owner, including typed discard events and
destination replacement. A suspended payment seals its selected reference;
load and resume validate the same reference without repeating payment or
rerolling the selection. Countering the spell retains the paid cost. Existing
random activation and resolution forms keep their established vocabulary.

A fixed reveal leaf shares the existing alternative additional-cost algebra.
Its hand query excludes the casting spell and pins one other qualifying owned
incarnation. Choosing the fixed mana branch adds that payment before ordinary
total-cost reductions; a free base mana cost still requires the additional
price. Offer, proposal and commit recheck the same current subtype predicate.
The hand-visibility owner discloses only the chosen card without moving it.
That card remains revealed until its spell leaves the stack, including through
resolution, countering or another canonical departure. Overlapping spells
retain their independent reveal durations. Ordinary prior disclosure and
learned identity survive the final duration ending.

Fixed additional tap prices use one identity-pinned reservation over current
untapped controlled permanents. A type union counts each object once, and
worded tap costs can use newly controlled creatures without Haste or targeting.
The offer search reserves those objects before the ordinary mana and payment
owners run; it searches other selections when an early subset would consume a
required mana source. Convoke and Improvise use separate objects, while Delve
retains its graveyard payment and actual remaining mana requirements. Proposal
and post-mana checks reject a changed incarnation, controller, type, phase or
tap state before the canonical tap group commits. Free base prices retain the
mandatory taps; countered spells retain their completed payments.

Fixed Waterbend costs use the same mana solver and canonical tap owner for
casting and battlefield activations. The compiler retains Waterbend's own
generic amount separately from the ordinary total requirements. Each selected
untapped controlled artifact or creature replaces one generic mana, up to that
amount; an artifact creature counts once. Source tap-symbol costs reserve the
source separately, while other newly controlled creatures may help.
The total cost receives ordinary reductions before a Waterbend payment is
selected. An exact payable-subset search leaves required mana sources available.
Both proposal and post-mana checks retain selected physical and logical identity.
The existing mana replacement continuation carries that reservation across
save/load. One normalized `player.waterbent` occurrence is published when the
cost is paid, including an all-mana payment. Countering the spell retains costs.
Optional, linked whether-paid, variable, complex and mixed payment families
remain outside this fixed contract.

Opponent spell costs may depend on the announced target set. A compiled static
component increases generic mana once when a spell targets its source or one or
more commander creatures its controller controls. Independent components add
their increases, followed by ordinary reductions. Current controller, phasing,
copied abilities and ability removal determine which components apply. The
selected cost option remains fixed while its mana is paid; this is a casting
cost rather than a Ward trigger. Activated abilities and stack copies do not
cast a spell and do not pay this tax.

The casting offer queries canonical target groups and retains payable target
witnesses for each cost signature. It preserves untaxed choices when another
legal target is too expensive. Optional and modal target choices include a
server-authored price table; choosing several qualifying targets still charges
each source once. The client displays that table and the server recomputes the
actual submitted target set through the same cost and legality owners.
An exact-X quote counts only targets compatible with that paid source
signature. An unaffordable taxed target cannot inflate an untaxed quote's
cardinality. Up-to-X selections retain their independently paid X even when
fewer compatible targets are available.

Preparation cards have normal permanent characteristics and separate copiable
prepare-spell characteristics. The physical card casts only with its normal
characteristics. A compiled enters-prepared replacement gives the entering
permanent a noncopiable prepared designation and creates one linked noncard
copy of its inset spell in exile. The existing copy owner allocates that object;
the state-based owner retains it only while its original permanent incarnation
remains present and prepared. Departure or unpreparing ends that exception.

Only the prepared permanent's current controller can cast the exile copy, using
ordinary timing, targets and payment. An offer or a failed payment preserves
the designation. The accepted cast unprepares the source after the spell enters
the stack; countering it does not restore preparation. Losing abilities or
becoming a different copy after preparation leaves the existing link intact.
A newly copied creature receives the alternative characteristics, but receives
prepared status only through its own entry replacement. Token copy exceptions
to its color or base statistics do not alter the inset spell's values.

Phase boundaries preserve the designation, end the old exile copy, and create
a new copy on phasing in. This lifecycle does not admit general phasing actions,
which retain their existing unsupported boundary. Broader prepare or unprepare
instruction grammar remains residual.

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
An unqualified source blocks/becomes-blocked pair consumes the canonical
declaration events: multiple blockers do not multiply its becomes-blocked
trigger. All defenders finish declaring before APNAP placement. The event pair
does not substitute the distinct per-blocker occurrence, and one source's block
cannot satisfy another source's subscription.

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

Multikicker result amounts read the immutable payment count of their linked
printed instance. Stack copies retain the original decisions without another
cast or payment. The zone owner seals an entering spell's fact into its entry
trigger before object-local reset; later departure cannot erase it, and a new
unpaid incarnation starts at zero. Entry facts are added only for typed readers,
preserving historical events with no count-result reader. Malformed or different
payment instances fail closed. Numeric evaluation preserves ordinary result,
target, private-choice, rollback and replay boundaries.

Spell result amounts that count spent mana colors use the canonical committed
WUBRG set. Payment validation still rejects overpayment, and cost increases,
reductions and alternate costs retain their normal order. Actual spell copies
use zero, independently of copied optional-cost decisions. Numeric projection
refines only existing typed result owners; fixed discard, draw and keyword
consequences continue after a zero variable result. Missing or malformed
payment provenance and readers outside spell resolution fail closed.

Modal effect projection uses the canonical target plan for group identities
and cardinalities, including flat mode definitions. Original selected counts
retain later-mode reference offsets after target departure. A flat mode's
target-set reference resolves through its server-captured current group, so
an illegal target is filtered without moving another mode's target role.
Missing or malformed captured groups fail closed. Existing explicitly grouped
reference projections retain their legacy representation.

Source-face target-dependent reductions use the same announced-target
witnesses for offers and accepted commands. One matching target applies one
printed reduction once, including when several targets qualify. The cost owner
adds increases before reducing generic requirements, preserves colored mana,
and locks the actual paid cost before responses. Captured cost schema 2 keeps
generic increases and reductions separate; historical tax schema 1 retains its
original bytes. X previews and public price tables retain each target set's
affordable bound instead of hiding a supported larger X or allowing an
underpaid different target. Copies and ordinary counter or invalid-target
resolution continue under their existing owners.

The current committed spell-cast event adds actual mana spent as a
nonnegative integer in schema version 7. The cast commit publishes the already
sealed paid stack fact, including additional costs and reductions; it does not
reconstruct payment from mana value or current prices. Earlier event versions
retain their exact field sets. Printed Increment consumes this fact through
the ordinary APNAP trigger path and rechecks current source creature P/T before
the existing counter-placement transaction. Independent instances recheck
after earlier counters; copies do not become casts.

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

Version-three permanent-state predicates add an explicit any-kind counter
minimum. `counter_snapshot.permanent_counter_total` validates and sums individual
public counters; zero entries do not qualify and malformed amounts reject.
Named-counter predicates and state schemas one and two keep their field sets.
Static queries use current counters after stabilization. Direct targets share
the same matcher and revalidate at resolution, while resolved characteristic
sets retain their incarnation locks. Public collection and source-total
quantities use this counter owner without reading later-layer characteristics.
An attack legally declared under a conditional Defender exception remains
declared when the final qualifying counter is removed afterward.

`continuous_stat_operations.py` applies fixed set, additive and switch
operations to detached characteristic proposals. Recipient-local scalar
modifiers use the same owner in layer 7c, reading that recipient after earlier
copy, type and color layers. Counter quantities read its canonical public
counter snapshot; ordinary counter P/T changes still apply separately. Mana
symbol quantities read the current copiable mana cost, including the empty
cost of a face-down object. `mana_symbol_counts.py` shares symbol validation
with collection and devotion counts. These effects retain live source and
subject applicability, validate their closed codecs and capability dependencies,
and store no mutable quantity cache. Existing operation payloads retain their
serialized fields.

Version-nine queries add `colors_none` for negative-color battlefield sets.
The shared matcher checks current color membership, so colorless objects match
and a multicolored object containing the excluded color does not. Static
characteristic effects recheck the live set after layer five; resolution
characteristic effects lock the current incarnations. Schemas one through eight
retain their serialized field sets. Specialized consumers keep their own
admission boundaries, and compiler/runtime binding both require the separate
negative-color query capability when the field is used.

Version-seven queries carry a fixed typed numeric characteristic predicate for
represented battlefield creature selection. They use the same comparison
value as direct targets and require exact effective stats. Live layer matching
and quantity expressions reject this field until their own read timing is
represented; adding it to a query does not implicitly grant those consumers.

The public battlefield compiler uses that same query for live layer-6 and
layer-7c applicability, public-state-gated targets, resolution-time set locks,
typed queried ability and declaration-fragment grants, and untap participation.
One descriptor may add fixed keywords and declaration fragments over the same
query without duplicating applicability. Landwalk as-though permissions remain
source-owned rule fragments: the block evaluator ignores only the selected
Landwalk variants and never removes an attacker ability. Attached-blocker
permissions consult the reciprocal current attachment identity, and cannot
follow a returned incarnation. Effective source ability loss ends a live
permission; ability loss on the recipient does not erase an Aura-owned rule. Positive ability presence is evaluated
after generic same-layer additions and removals of the required ability.
Resolution-time characteristic selection rejects ability-qualified sets.
Its explicit numeric creature filter reads completed effective power/toughness
before creating the new effect and locks the selected incarnations. Live
static quantities retain the separate layer-5 boundary.

One conditional self-color and typed-ability compound begins at layer 5. Its
fixed color replaces the previous colors; its ability and additive portions
apply in layers 6 and 7c. Source participation is checked before layer 5, so
later ability removal preserves already-started portions under CR 613.6.
Layer-6 timestamps still determine whether a separately removed grant remains.
Current controller facts govern the live condition. Losing that condition
ends new activation and trigger availability while stack abilities retain their
original controller and ordinary target revalidation.

Schema-five public conditions have a separate numeric creature-existence
predicate. Intervening triggers and resolution instructions read complete
current effective power or toughness under their locked controller. They
recheck intervening conditions at occurrence and resolution, include counters
and represented buffs, and reject unavailable numeric facts. Static
characteristic conditions reject this schema because their cycle-safe read
boundary remains at layer five.

`rules/defender_declaration.py` evaluates the separate numeric Defender
exception after the full characteristic query has finished. The source's
current trusted face and ability must still provide the declaration component.
Offers and acceptance share this check, which disregards only Defender while
the numeric condition holds. A later condition change affects the next
declaration, without removing a creature from an already declared attack.

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

Targeted activation announcement and payment share the ordinary stack owner.
The announcement exists inside the command transaction before costs can move
its source. Canonical zone transitions capture its source information, and
payment finalization preserves that frame rather than rebuilding it from the
post-cost card. Failed and suspended payments retain the command's atomic
rollback boundary. [Target legality](target-legality.md) owns the acting-object
and source-information distinctions.

Version-four intrinsic entry-copy selectors carry one closed eligibility
predicate. The owner uses the entering controller's current-turn declared
attack journal, the candidate card's exact current graveyard-arrival incarnation,
or the entering spell's actual accepted mana payment. Noncast entries and spell
copies have zero spent mana. Graveyard arrival is distinct from creature death:
it records origin and arrived incarnation even when a noncreature permanent
becomes a creature card in that zone. Copy choices remain nontargeted and use
the existing immutable replacement preparation and selection journal.

Version-five intrinsic copy entry carries a closed conditional exception for an
explicit Embalm creation origin. The graveyard-copy recipe owns the noncopiable
creation designation; generic token status or copied name does not supply it.
Prospective token objects use the same intrinsic copy snapshot coordinator as
physical cards. The shared entry-result owner validates and commits copied
values before token entry observers, while the token owner retains allocation,
counter and trigger ownership. Added Changeling is the existing copiable
layer-4 CDA, so layer-6 ability removal does not undo its earlier type effect.
