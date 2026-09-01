---
title: "Counter placement and removal transactions"
status: "current"
authoritative_source: "quorune/counter_placement.py, quorune/counter_removal.py, quorune/counter_names.py, quorune/counter_state.py, quorune/counter_maximums.py, quorune/counter_placement_sets.py, quorune/counter_placement_targets.py, quorune/damage_results.py, quorune/player_result_events.py, quorune/token_creation.py, quorune/keyword_counters.py, quorune/attachment_references.py, quorune/entry_counter_model.py, quorune/entry_counters.py, quorune/fixed_keyword_entry_counters.py, quorune/saga_progression.py, quorune/turn_counter_coordination.py, quorune/death_return.py, quorune/unleash.py, quorune/mentor.py, quorune/attack_counter_triggers.py, quorune/renown.py, quorune/modular.py, quorune/amass.py, quorune/zone_object_subtype_grants.py, quorune/relative_power_target.py, quorune/target_predicates.py, quorune/permanent_designations.py, quorune/zone_object_state.py, quorune/compiler/amass_templates.py, quorune/compiler/counter_maximum_templates.py, quorune/compiler/counter_removal_templates.py, quorune/compiler/fixed_counter_trigger_nodes.py, quorune/compiler/fixed_keyword_entry_nodes.py, quorune/compiler/fixed_self_entry_counter_templates.py, quorune/compiler/fixed_target_effect_sequences.py, quorune/compiler/fixed_source_effect_sequences.py, quorune/compiler/self_counter_keyword_actions.py, quorune/rules/optional_counter_capability_shapes.py, semantic_runtime/ability_fragments.py, semantic_runtime/counter_replacements.py, semantic_runtime/counter_removal_handlers.py, semantic_runtime/token_replacements.py, semantic_runtime/zone_replacements.py, semantic_runtime/self_entry_counters.py, semantic_runtime/block_restrictions.py, semantic_choices/amass.py, semantic_choices/death_return.py, semantic_choices/modular.py, semantic_choices/optional_counter_placement.py, ADR 0011, ADR 0034, ADR 0036, ADR 0037, ADR 0038, ADR 0039, ADR 0048, and ADR 0054"
verified: "2026-08-22"
audience: "rules, semantics, replay, and architecture contributors"
maintenance: "hand-maintained"
---

# Counter placement and removal transactions

`counter_placement.py` is the focused authoritative owner for represented
effect-generated, cost-generated, and typed rule-result counters placed on
players, battlefield permanents, and the already modeled card-zone counter
children. It separates the operation into preparation and commit:

1. Resolve each subject and build immutable player- or object-affected
   `counter.place` events.
2. Discover active trusted runtime descriptors against the pre-mutation state.
3. Traverse simultaneous events in APNAP order and let the affected player or
   permanent's controller choose among represented applicable replacements.
4. Suspend through the ordinary seat-scoped replacement continuation when a
   real choice exists.
5. Commit only after every selection is complete, every player still exists,
   and every permanent is still the same object in the expected zone.

This order enforces the represented portions of CR 122.6, 614.1, 614.16,
616.1, 616.1f, and 616.1g without giving pure runtime components mutable state.
The choice projection contains labels and stable option IDs only; the event
payload, object identifier, replacement batch, and prior journal remain in the
authoritative continuation. Exact replay reconstructs and validates the path,
chooser, and selected effect.

`replacement.counter.quantity.v2` is the current bounded component. It applies
fixed positive integral multiplication or fixed nonnegative addition to a
represented placement on a player or battlefield permanent. Its closed
descriptor distinguishes effect-only wording from all-placement wording and
may restrict the placing player, affected player/permanent relation, counter
name, and a small validated set of effective card-type or subtype predicates.
`replacement.counter.quantity.v1` remains registered only for replay and
reviewed-pack compatibility; new compiler output uses v2.

A containing typed replacement tree may exhaust counter replacement ordering
before it reaches this owner. `plan_resolved_counter_placement_commit` accepts
only immutable, childless `counter.place` leaves with unique event identities
and performs no rediscovery. Damage results use that boundary so a quantity
replacement applies exactly once; the final placement still receives the same
logical-object validation and counter-state commit as an ordinary placement.

The generic compiler lowers exact ordinary "an effect would put," "you would
put," and passive "would be put" quantity-replacement sentences. It rejects
fractional, halving, dynamic, optional, team, opponent, "another," and
Class-level-gated variants as material residuals. Doubling Season's
effect-only wording therefore does not change a positive loyalty-symbol cost,
while represented all-placement wording such as Doc Samson's does.

Zone-destination replacements use the closed
`CreateAffectedObjectCounter` operation to derive a typed child from the
parent zone event. The operation binds the affected physical object and the
already transformed destination at application time, so one immutable source
effect can serve every event in a simultaneous batch. The containing zone
event is exhausted before its child is considered. Every replacement choice
is complete before the move; the child counter commits only after the card
reaches its validated destination. A counter on a card outside the battlefield
is represented for ordering but remains outside the permanent-only quantity
component.

The Oracle compiler lowers the closed “an opponent's card from anywhere would
enter a graveyard; exile it with one named counter instead” family to this same
destination handler and nested counter operation. Different owners, origins,
object kinds, optional wording, counter-free moves, and alternate destinations
remain residual rather than being inferred at runtime.

## Ownership and dependencies

`counter_placement.py` depends on immutable replacement values and narrow host
protocols. It delegates the one atomic write plan to `counter_state.py`, which
owns poison, energy, arbitrary normalized player counters, and permanent
counter maps.

`counter_removal.py` owns three deliberately distinct permanent-counter
transactions over that same state boundary. Rule requirements and costs use
the exact batch plan and fail before mutation unless every requested counter
exists. The fixed effect-result plan instead snapshots one named counter kind,
removes the lesser of the requested and available quantities, and permits a
zero-change result. The all-counter effect-result plan snapshots every positive
counter kind in canonical order and commits them as one exact batch, including
an empty no-op result. These effect plans implement the represented CR
101.3/609.3 result boundary without weakening exact payment semantics. Every
plan pins the permanent's current logical identity and expected zone and
validates again at commit.

Oracle IR lowers mandatory fixed named-counter removal and mandatory removal of
all counters from one direct battlefield permanent target through spell,
triggered, and activated CardProgram contexts. Both share the closed target
grammar with fixed counter placement, use the existing target offer and
resolution-time revalidation owner, and emit typed `RemoveCountersIntent` or
`RemoveAllCountersIntent` values; no handler interprets Oracle text or knows
card identities at runtime. Removing the last defense counter uses the
existing intrinsic Siege trigger boundary. Optional, variable, distributed,
repeated, named-kind all-counter, unspecified-kind fixed, player-counter,
movement, cost, linked, modal, and compound variants remain residual.
`counter_placement_sets.py` and `counter_placement_targets.py` are read-only
coordinators: they snapshot a represented public battlefield set or the
still-legal members of a submitted bounded target set, canonicalize it by
APNAP controller and logical object identity, and delegate the complete batch
to `counter_placement.py`. Neither module owns authoritative state mutation.
`semantic_runtime/counter_replacements.py` validates source descriptors and
returns immutable effects; architecture policy prohibits it from importing the
engine, `GameState`, transport, persistence, or projection code. Positive
loyalty-symbol costs enter this same transaction through the typed activation
commit owner with `effect_generated=false`. A stable private cost-event ID and
strict priority-action continuation allow competing replacements to suspend
before any cost or stack mutation and resume with exact replay. Client commands
cannot supply those internal continuation fields. Infect, Wither, and Toxic
damage-result leaves also enter the placement owner with their final containing-
tree result and pinned logical identity.

`counter_removal.py` is the distinct exact-removal owner. Represented
Planeswalker loyalty and Battle defense damage results share it with stun and
state-based counter removals; replacement-aware placement and exact removal do
not compete for the final counter-map write boundary.

CR 704.5r maximums enter that owner through a current typed ability view.
`counter_maximums.py` defines the immutable fixed self-maximum descriptor, and
Oracle IR lowers the bounded numeric sentence using full, shortened, or
this-object source references before a game starts. State-based snapshots read
only the effective ability fragments after copy and layer-6 changes, choose the
strictest current maximum for each counter kind, and combine that requirement
with other simultaneous counter removals. Dynamic values, another-object or
player maximums, compound instructions, and unrepresented ability grants remain
material residuals; runtime code never recovers them from display text.

The engine retains compatibility facades and supplies the host protocol. New
positive fixed counter operations must enter the transaction instead of adding
another direct engine write. Effect- or cost-generated removal, effects that
prohibit placement, combined or modified loyalty-symbol costs, counter
movement, and other unrepresented rule actions remain distinct and fail closed
until their ordering semantics are modeled.

Fixed positive Adapt and Monstrosity use the same transaction. Their activation
remains available independently of the current resolution condition. The
strict handler resolves the current source incarnation, checks for existing
+1/+1 counters or the monstrous designation, and then emits the ordinary
counter intent. Monstrosity follows that intent with the typed designation
transition, so replacement can change the counter result—including to zero—
without preventing the permanent from becoming monstrous. The designation is
public and noncopiable, survives control changes and phasing, and is cleared by
the extracted object-local CR 400.7 reset when a zone change creates a new
object. Variable, zero, compound, granted, copied, and value-consuming variants
remain material residuals.

Closed target- and source-threaded sequences may now place one fixed counter
and then apply a represented fixed characteristic result in printed order.
Target sequences pin and revalidate target zero; source sequences resolve the
exact current source zone object and pin its logical identity before any
replacement suspension. Counter placement still owns replacement preparation
and mutation, while the separate continuous-effect owner commits the later
layer-6 or layer-7c result. A stale target/source or failed later commit rolls
back the complete resumed transaction. Optional, variable, compound, chosen,
multi-target, and arbitrary granted-ability text remain residuals.

The fixed counter/controller sequence family separately composes exactly one
source or direct-target fixed counter instruction with exactly one controller
draw, life-change, or Scry instruction in either printed order. A source
placement resolves through `$source.zone_object`, so departure makes only that
instruction inapplicable while an independent controller instruction still
resolves. Counter replacement may suspend after an earlier controller result
without replaying it; the continuation carries only the remaining printed
instructions. This family adds no counter write path and excludes optional,
modal, conditional, variable, linked, repeated, movement, and affected-set
variants.

## Current producer inventory

The shared transaction currently owns the typed `place_counters` operation,
legacy-compatible positive `add_counter_selected`, positive generic `counter`,
`counter_all_subtype`, direct transaction calls, typed nested zone-replacement
counters, ordinary positive-integral Fabricate choices, the conditional +1/+1
counter from one permanent exploring once, and ordinary single-instruction
Proliferate over players and permanents. These paths prepare before mutation
and can safely suspend.

Fixed ordinary-mana Level Up, Outlast, Reinforce, and fixed printed-power
Scavenge also enter through `place_counters`. Their compiler-pinned activation
descriptors own timing and source-zone costs, while this transaction continues
to own counter replacement, APNAP choice, rollback, privacy, and replay. A
battlefield source result is pinned to the activating incarnation; Reinforce
and Scavenge revalidate their creature target. Variable Reinforce and dynamic-
power Scavenge remain outside trust.

Ordinary fixed counter activations also reuse that operation when their whole
effect body is already represented and any trailing activation restriction is
exactly captured by typed discovery metadata. The compiler removes only closed
controller-turn, sorcery-speed, once-per-turn, token-history, controlled-type,
or graveyard-type tails after verifying the matching typed descriptor; upkeep,
step, conditional-history, and mixed unrepresented tails remain material.
Source-self Aura, Equipment, Saga, Spacecraft, and Vehicle wording lowers to the
same physical `$source` identity as card-type or bounded name wording. These
printed descriptors do not become runtime type predicates, so the counter
transaction continues to validate object identity rather than reinterpreting
current characteristics.

Ordinary printed Station uses the same transaction for its charge-counter
result. One typed activation owner advertises and commits exactly one other
untapped controlled creature, pins that creature's physical and logical
identity on the stack, and reads its exact current power only as the ability
resolves. The zone-transition owner captures immediate predeparture power as
rollback-safe last-known information; negative power produces zero counters,
and a departed or returned Station source is not affected. Sorcery timing,
summoning-sick cost creatures, quantity replacement, multiplayer choice
privacy, and exact replay remain ordinary shared-owner behavior. CR 721 Station
symbols, CR 702.184c characteristic substitution, cost-creature phasing, and
type changes that remove creature power before resolution remain explicit
fail-closed boundaries.

Mandatory fixed counter effects on represented trigger families enter through
the same `place_counters` operations. The compiler binds closed upkeep,
end-step, and beginning-combat schedules; controlled-land entries; controller
noncreature or instant-or-sorcery casts; controller life gains; controller card
draws; the controller's exact second draw; and one closed family of public
artifact, creature, enchantment, or permanent entries and creature deaths to
normalized event facts. The zone-change family accepts only typed controller,
opponent, source-exclusion, token, and one exact public one-word subtype
predicate. A source-or-another subtype form matches the source identity directly
and every other permanent through the immutable subtype tuple carried by the
normalized entry occurrence. Departure predicates consume the previous
controller and logical object identity captured before mutation, so a
represented source can observe its own or a simultaneous death without
recovering last-known information from prose. Draw events expose only public
player and ordinal facts. Positive life-gain events are emitted after the
canonical replacement-capable effect or semantic-choice life transaction,
Lifelink result, or prevention aftermath commits. The trigger subsystem performs
ordinary APNAP placement and the effect body retains existing target and
replacement semantics. This adds no counter write path and no runtime Oracle
parser. Multi-subtype, multiword-subtype, characteristic-qualified, one-or-more
aggregated, alternate-zone, and combined zone-change triggers remain residual.
Granted, copied, or removed instances remain outside trust until all static
components share the layer-6 ability-presence query. Cast predicates consume a
sealed committed snapshot: validated cast-selected types, subtypes, and
supertypes plus cycle-safe effective stack colors. Static single or two-way
alternatives over those fields, including colorless and multicolored, are
represented; dynamic counts, history, origin, payment, targeted-spell,
mana-value, conjunction, and same-layer stack-characteristic interactions
remain excluded.

Those same closed event and effect-body families accept an exact leading “you
may” as a separate optional capability. Resolution issues one controller-owned
put-or-decline semantic choice. Declining completes without mutation;
accepting prepends the already validated fixed counter operation and therefore
retains its ordinary target revalidation, quantity-replacement ordering,
rollback, privacy, and replay behavior. The choice handler validates the nested
operation through the shared typed semantic interpreter and never commits a
counter itself. Optional effects outside these represented event triggers,
multiple instructions, variable amounts, linked “if you do” results, and other
conditional forms remain residual.

The same `place_counters` operation also owns one closed multi-subject family:
two or three printed-order source/direct-target permanent subjects receive the
same fixed positive quantity of one counter kind. Each direct target is
revalidated independently. Repeated target instances may select the same
permanent unless the printed wording says “another” or “a third”; those words
compile to explicit distinctness constraints rather than a runtime Oracle-text
check. A source that left before resolution is skipped while remaining legal
targets still resolve. Every surviving subject enters one immutable
replacement-aware `PlaceCountersIntent`, so APNAP choice, rollback, privacy,
and replay reuse the existing counter transaction rather than a second
mutation path.

One optional bounded target-set owner accepts the closed “on up to N target”
and “on each of up to N target” forms for represented permanent types,
controller relations, and the typed tapped-creature state predicate. Choosing
zero targets is a complete legal activation or
cast choice, not a skipped instruction: non-target costs still commit, the
stack object still resolves, and the resulting empty placement batch performs
no mutation. Selected targets are revalidated independently before the shared
replacement-aware transaction. Variable quantities, subtype and combat-state
predicates, linked choices, and compound results remain residual.

Intrinsic Planeswalker loyalty and Battle defense now use the same boundary.
The card-form compiler reads the canonical parsed type set and printed integral
characteristic once, emits a type-line-spanned CardProgram declaration, and
requires `counter.producer.intrinsic_entry`. Entry preparation lowers that
declaration to a mandatory self-replacement on the containing zone event; its
typed nested counter event follows any later destination replacement before
the ordinary affected-controller quantity-replacement ordering. A resolving
permanent can suspend through `resolving_entry` and resume without replaying
earlier spell effects. Simultaneous entries prepare in APNAP order without
mutation.

Planeswalker and Battle tokens reserve immutable prospective refs, physical
object IDs, logical identities, and a shared entry timestamp before mutation.
The token owner first exhausts represented additional-token replacements, then
prepares one simultaneous intrinsic-counter batch against those prospective
objects. A later quantity-replacement choice suspends through the same strict
selection journal. Commit allocates the exact reserved identities, creates the
tokens, and applies the prepared counters; the former replacement-free token
counter bypass has been removed.

Saga lore uses deliberately distinct entry and turn-progression paths. The
card-form compiler emits ordinary `counter.producer.saga_lore` from the parsed
Saga subtype and treats the exact CR reminder line as provenance rather than a
runtime parser. Printed Read Ahead instead compiles one source-spanned keyword
handler only when its printed chapter symbols are contiguous and matching
trusted typed chapter programs exist at runtime. Its destination controller
chooses a chapter through the private replacement journal; the chosen amount
becomes a child counter event inside the same zone-change tree. Represented
effect-qualified quantity replacements therefore apply before the permanent
enters. Ordinary Sagas dispatch every crossed trusted chapter, while CR
702.155a permits a Read Ahead entry-turn chapter only when the final lore count
exactly equals that chapter number. At the active player's precombat main boundary,
`saga_progression.py` snapshots every controlled Saga with trusted typed
chapter declarations and prepares one simultaneous `counter.place` batch with
`effect_generated=false`. Unqualified replacements that apply when that
player would put counters participate in the ordinary affected-controller
ordering, while effect-qualified text such as Doubling Season remains
inapplicable. `turn_counter_coordination.py` suspends a competing order before
mutation, pins the phase and event identities, and resumes the same batch
after save/load. All resulting lore changes commit before any crossed chapter
is dispatched, and those triggers join the same waiting-trigger batch as
other beginning-of-phase triggers. The separate `state_based.saga_final_chapter`
capability snapshots the exact Saga incarnation, waits while one of its typed
chapter abilities is pending, and then routes the ordinary final-chapter
sacrifice through the simultaneous state-based zone-change transaction.
Untrusted chapter programs, arbitrary lore-counter movement, and copied,
gained, removed, or layer-modified Read Ahead or chapter abilities remain
fail-closed.

Effect-generated entry counters use the same nested replacement tree through
an immutable `EffectEntryCounter`. The instruction pins the physical card's
expected zone-change counter, prospective battlefield controller, placing
player, source, counter name, amount, and rule identity. Semantic preparation
completes every represented destination and counter-quantity replacement
choice before committing the move. A missing card, stale incarnation,
inactive placing player, non-battlefield destination, or malformed counter
fails or safely makes an explicitly optional return do nothing before state
mutation.

Printed Persist and Undying harvest that generic boundary. The compiler emits
one source-spanned triggered CardProgram per
printed keyword instance. Trigger discovery evaluates the relevant counter
from the departed creature's last-known public snapshot, preserves the
graveyard incarnation and trigger controller, and places simultaneous triggers
in the existing APNAP batch. Resolution returns only that same graveyard
incarnation under its owner, then applies the required -1/-1 or +1/+1 counter
through the effect-entry transaction. Control changes, duplicated keyword
instances, destination and quantity replacements, tokens, private projection,
and replay therefore share existing owners rather than keyword-specific state
writes. Granted or copied instances outside trusted typed ability fragments,
Oracle-equivalent prose, and unrepresented replacement families remain
explicit residuals.

Ordinary printed Unleash now adds a separate optional self-entry producer.
Oracle IR emits two independent typed programs from the same exact keyword
span: an all-zone affected-object entry replacement that offers one additional
+1/+1 counter,
and a battlefield block prohibition that reads the permanent's current public
counter snapshot. Each printed instance creates its own apply-or-decline
replacement, the prospective controller chooses before entry mutation, and an
accepted counter enters the same nested quantity-replacement tree described
above. The final counter state feeds the shared block-legality adapter used by
both projected options and accepted commands. Nonkeyword equivalents and
granted, copied, lost, or face-down Unleash outside typed ability propagation
remain explicit residuals; this slice does not broaden aggregate replacement
or blocking claims.

Ordinary printed Riot uses a separate linked entry-choice capability. Each
printed instance creates one optional affected-object replacement: applying it
creates a nested replacement-aware +1/+1 counter event, while declining it
creates an identity-pinned layer 6 Haste grant for that battlefield
incarnation. Both paths are selected by the prospective controller before the
zone mutation commits. The Haste result persists through cleanup, ends when
the object leaves the battlefield, and is consumed by the existing attack and
tap-or-untap-cost legality owners. Multiple Riot instances remain independent.
Nonkeyword equivalents, alternative results other than Haste, and granted,
copied, lost, or face-down Riot outside typed ability propagation remain
explicit residuals; aggregate entry replacement and continuous-effect claims
remain bounded.

Ordinary printed Mentor uses the same counter transaction without acquiring a
keyword-specific mutation path. The compiler emits one source-spanned typed
ability fragment per printed Mentor instance. A completed attack declaration
captures the source's effective power, creates independently identified
targeted triggers in the shared APNAP batch, and offers only current attacking
creatures with strictly lesser power. Resolution revalidates both creatures and their current effective
powers. If the Mentor source left before resolution, a typed departure snapshot
provides its immediate predeparture power while preserving the original logical
source identity; simultaneous departures capture every referenced source before
any move commits. A source that is currently, or immediately before departure,
a noncreature permanent has no power under CR 208.3; its printed power cannot
make the target legal. The result is one +1/+1 counter placed through the
canonical replacement-aware transaction. CR 702.134c's separate “mentors another
creature” event, granted or copied Mentor outside typed ability propagation,
prose equivalents, attackers put onto the battlefield outside declaration,
source phasing without a typed phase-out snapshot, unsupported characteristic
families, and trigger-doubling policies remain explicit residuals.

Ordinary printed Dethrone and Training now share a second typed
declaration-time counter-trigger owner. The completed attack transition pins
public recipient relationships and exact source identity once. Dethrone adds a
canonical snapshot of every active player's public life total and qualifies
only a direct attack against a player tied for greatest life; attacks against
planeswalkers or Battles do not qualify. Training adds a canonical effective
power snapshot for every declared attacker and requires another creature with
strictly greater power. Later life or power changes do not alter a trigger that
already exists. Each printed instance creates an independently identified
occurrence in the ordinary trigger batch. Resolution affects only the same
logical source incarnation, still on the battlefield and not phased out, but a
control change does not change that identity. The +1/+1 result delegates to the
same replacement-aware counter transaction as other fixed results. CR
702.149c's distinct “trains” event and listeners, granted or copied keyword
propagation, nonkeyword equivalents, attackers put onto the battlefield, and
trigger multipliers remain explicit residuals; neither aggregate mechanic is
trusted by this bounded family.

Ordinary printed positive-integral Renown uses the final normalized damage
result rather than attack declaration. One source-spanned typed fragment is
emitted for every printed instance. A positive combat-damage result whose
final recipient is a player creates the ordinary trigger while the same source
incarnation is phased in and not renowned; this includes damage redirected to
the source's controller. Resolution rechecks that intervening condition. It
then places the fixed +1/+1 counters through the canonical replacement-aware
transaction and applies a separate public, noncopiable renowned designation.
The designation still occurs when replacement commits zero counters, survives
control changes and phasing, and clears when a zone change creates a new
logical object. A copy does not inherit it. Both mutations are one transaction,
so a designation failure rolls back the preceding counter result. Multiple
Renown instances trigger independently, but after the first successful
resolution later instances fail the shared intervening condition. Variable or
zero values, Oracle-equivalent prose, renowned-matters listeners, trigger
multipliers, and granted or copied Renown outside trusted typed ability
propagation remain explicit residuals.

Ordinary printed positive-integral Modular compiles as two source-spanned
typed fragments per keyword instance. Its mandatory self-entry half reuses the
generic prospective zone-change counter handler and canonical counter
replacement transaction. Its battlefield-to-graveyard half snapshots the
departing incarnation's complete public counter map and current controller
before mutation, selects one currently legal artifact creature target through
the ordinary trigger path, and offers the LKI-sized +1/+1 counter placement on
resolution. Target legality is rechecked against current typed characteristics,
and the counter result uses the same replacement-aware owner. Multiple fixed
instances remain independent. Modular—Sunburst, nonpositive or variable
values, Oracle-equivalent prose, trigger multipliers, and granted or copied
Modular outside trusted typed ability propagation remain explicit residuals.

Printed positive-integral Fading, Graft, and numeric Vanishing share the same
mandatory self-entry component. The compiler selects fade, +1/+1, or time
counters from the typed keyword descriptor, emits one independent all-zone
entry program for each printed instance, and routes the prospective placement
through the existing nested zone-change and quantity-replacement transaction.
It also retains a separate material lifecycle residual on the same keyword:
Fading upkeep removal and sacrifice, Graft's enters trigger and counter move,
and Vanishing upkeep removal and last-counter sacrifice remain unrepresented.
Bare Vanishing, nonpositive or variable values, Oracle-equivalent prose, and
granted, copied, or removed abilities outside the future shared typed
ability-presence boundary also remain residual rather than gaining a
family-specific runtime check.

Generic fixed prose self-entry counters use that same all-zone owner. The
compiler accepts only one complete source sentence in which this object or its
bounded printed name enters with one through ten counters of one canonical
kind on itself. It records the amount and counter kind in the immutable
replacement descriptor and routes the placement through the existing nested
zone-change and quantity-replacement transaction. Variable, zero, larger,
optional, conditional, additional, multikind, linked, and another-object forms
remain material residuals; the grammar does not infer or parse them at runtime.

Ordinary printed Sunburst now has a dedicated cast-payment entry owner. Cast
commit freezes the distinct WUBRG colors actually spent on the spell as a
typed stack fact; stack copies deliberately receive an empty fact because they
were not cast. The compiler selects +1/+1 counters for a printed creature face
and charge counters for every other printed face before runtime characteristic
evaluation, matching CR 702.44a's instruction to ignore type-changing effects
without introducing a characteristic dependency cycle. A resolving card
carries the frozen colors into its immutable prospective zone-change snapshot,
and each printed Sunburst instance creates one replacement-aware counter event.
Colorless casts, entries from outside the stack, and spell copies produce no
Sunburst counters. Modular—Sunburst linkage, nonkeyword equivalents, and
granted, removed, copied, or face-down abilities outside typed propagation
remain explicit residuals.

Oracle IR v76 lowers the closed reusable fixed-placement grammars through the
typed operation in spell, triggered, and activated contexts. It accepts one
positive exact quantity of one named counter on the source, the exact named
source, or one direct battlefield permanent target. Direct targets may use one
permanent card type or one pinned creature subtype, a fixed controller
relation, and source exclusion. The strict runtime handler lowers only to
`PlaceCountersIntent`; it neither parses Oracle text nor mutates state.

The v70 group grammar composes that same single-subject grammar for two or
three source/direct-target subjects when every clause has the same counter kind
and amount. It preserves printed subject order, explicit target reuse or
distinctness, controller relations, an optional final target, and the exact
typed Commander predicate used by “target commander creature.” Different
amounts or counter kinds, variable or distributed values, optional nonfinal
subjects, more than three subjects, ambiguous compound wording, and dynamic or
linked quantities remain precise material residuals.

Oracle IR v64 adds one closed target-threaded sequence grammar for two or three
mandatory sentences that share direct creature target zero. Exactly one
sentence establishes the target; later clauses may refer to it only as “it.”
The represented sequence must contain one fixed counter placement and at least
one fixed power/toughness change or ordinary keyword grant until end of turn.
The compiler preserves printed operation order, emits one source-spanned
CardProgram node, and declares the counter, targeting, continuous-effect, and
individual keyword-mechanic dependencies actually used. Optional, modal,
conditional, variable, repeated, multiple-target, protection-choice, search,
scry, naming, and animation variants remain material residuals.

Ordinary CR 122.1b keyword counters now feed layer 6 through the typed
`keyword_counters.py` vocabulary. A positive represented counter grants its
named ability; removing the final counter removes that contribution. The
counter owner validates exact nonnegative quantities and has no Oracle-text or
card-name path. This characteristic projection does not by itself certify the
runtime behavior of every keyword: CardProgram closure still requires the
specific Flying, Haste, Trample, Hexproof, Indestructible, or other mechanic
capability. Parameterized keyword variants and same-layer dependency cases
outside the current continuous-effect model fail closed.

The interaction inventory treats the counter-to-keyword boundary as
high-risk wherever it crosses another rules owner. Current focused composition
evidence covers replacement-aware placement before the ability appears,
Flying block legality, Vigilance attacker tapping, Double strike damage-step
participation, Lifelink's final damage result, Deathtouch assignment and result
processing, Trample spill assignment, Menace block declarations, and Hexproof
target offers plus resolution revalidation. Those owners all consume the same
effective characteristic view used by projection; none reinterprets the
counter name. The evidence does not promote Decayed or Exalted triggers,
unrepresented keyword semantics, or same-layer ability-removal dependencies.

Oracle IR v65 lowers one mandatory instruction that places two or three
distinct fixed counter kinds on the source permanent or one direct permanent
target. The compiler preserves printed counter order and emits one
`PlaceCounterBatchIntent`; the runtime resolves the shared recipient once and
submits every placement as one simultaneous request sequence to the canonical
counter transaction. Replacement ordering therefore completes for the whole
instruction before any counter changes. Keyword counters additionally require
their typed layer 6 characteristic capability and the independent runtime
capability for the granted keyword. Optional, variable, repeated, duplicate,
  distributed, multi-subject, player, entry, and affected-set variants remain
  material residuals.

The source-threaded sequence grammar is deliberately smaller than the target
grammar. It accepts one positive fixed source counter followed by one fixed
power/toughness or represented keyword result until end of turn, all against
`$source.zone_object`. It is used by independently capability-closed activated
abilities such as the bounded Exhaust harvest; it is not an Exhaust-specific
runtime path. Effects that allow another Exhaust use, add choices or clauses,
or refer to a source that has left and returned remain fail-closed.

The attachment-relative fixed-placement family adds one typed semantic
reference for the object a source enchants, equips, or fortifies. The compiler
requires the exact parsed Aura, Equipment, or Fortification source subtype and
one closed permanent-type recipient; mismatched or dynamic qualities remain
residuals. Activation commit captures the reciprocal source/target identity
before costs, and trigger discovery captures it before enqueueing. Resolution
uses the live relation while the same source incarnation remains or the pinned
last-known relation after it leaves, then rejects a phased, wrong-type, or new
target incarnation before the existing counter intent is created. The
read-only identity resolver adds no state mutation or runtime Oracle parser.

The affected-set family lowers one mandatory fixed quantity onto every member
of one closed public battlefield set. Its predicates are serialized in an
immutable `AffectedPermanentSetSpec`; represented sets may test one named
counter, current-turn battlefield entry, a closed color qualifier, or a closed
creature-subtype disjunction. Resolution derives entry history from the
authoritative positive turn sequence and snapshots the entire set before the
canonical simultaneous counter transaction begins. The bounded
target-set family separately lowers “each of up to N target” instructions for
direct permanent types, optional controller relations, the represented
noncreature-artifact predicate, and tapped creatures. The selected refs remain
distinct and bounded, zero is legal for “up to,” and resolution follows CR 608.2b: still-legal
targets receive counters, while an originally nonempty selection with no legal
targets does not resolve. Both families use typed semantic intents and exact
replacement continuations rather than runtime Oracle interpretation.

The fixed positive Support N family reuses that target-set path. The compiler
derives source context from the exact parsed card-type set: a permanent source
adds the CR 701.41a “other” source exclusion, while an instant or sorcery
source does not. Unrelated or ambiguous source types remain residual. Support
then resolves as one +1/+1 counter on each surviving creature target through
the existing APNAP-canonical, quantity-replacement-aware transaction; it adds
no runtime Oracle parser, Support-specific mutation, or card identity branch.

The fixed positive Amass subtype N family composes three existing owners rather
than introducing an Army-specific state write. If the resolving controller has
no Army, one typed token-creation intent first traverses represented additional-
token replacements. The handler then requeries the public battlefield and
either selects the only Army or issues the controller a stale-safe choice among
multiple current Army incarnations. Its +1/+1 result enters the canonical
counter-placement transaction; only after that replacement-aware result
commits does a separate locked layer-4 effect add the named creature subtype to
the same battlefield object. The effect expires when that Army leaves, while
control changes preserve the affected zone object. Current Oracle plural forms
are validated against the pinned CR 205.3m subtype registry and lower to one
singular typed value. Amass X, zero, conditional, repeated, compound, and linked
“Army you amassed” clauses remain explicit residuals, as do unrepresented token
prohibitions and replacement families.

The same compiler boundary now lowers one mandatory fixed player-counter
instruction in spell, triggered, and activated contexts. Its closed relations
are the controller, one direct active-player target, each active player, and
each active opponent. Energy and ticket symbols lower to their canonical
counter names; ordinary named poison, rad, energy, ticket, and experience
counters use the same typed `PlacePlayerCountersIntent`. Simultaneous subjects
are APNAP-canonical, direct targets are revalidated immediately before commit,
and every write remains owned by `counter_state.py`. The capability now declares
the counter-quantity replacement owner as a dependency rather than relying on
the transaction implementation implicitly. Source-named “CARDNAME or another
artifact” triggers normalize only against the exact compiling card name, so
Gonti's Aether Heart now contributes a source-spanned capability-closed trigger
instead of a reviewed semantic-pack trigger overlay. Variable quantities,
linked subjects, multiple player-counter kinds, and player-counter quantity
replacement or prevention wording remain residual and fail closed.

The legacy `energy` effect operation remains accepted only for replay and saved
registry compatibility. Positive exact amounts now enter the same player-counter
transaction, receive applicable quantity replacements, and emit canonical
counter events; nonpositive, boolean, inactive-seat, and malformed replacement
inputs fail before mutation. The deterministic Mishra/Gonti shortcut remains an
explicit aggregate simulation boundary: it still bypasses per-trigger
replacement ordering and therefore does not supply trust evidence for affected
replacement interactions. Migrating that shortcut requires a resumable
per-iteration frame rather than treating `4 * repeat_count` as one placement.

The bounded Proliferate family compiles an unmodified `Proliferate.` clause in
spell, triggered, and activated contexts to CardProgram V2. The resolving
controller chooses any number of eligible public subjects. The continuation
pins physical and logical permanent identity plus every positive counter kind;
one additional counter of each kind then enters one simultaneous
replacement-aware batch. The transaction permits an empty selection and
rejects a changed subject or counter-kind snapshot before any counter changes.
Two-Headed Giant shared poison totals, repeated or variable Proliferate,
Proliferate replacement effects, and broader granted/copy propagation remain
explicitly unsupported.

The bounded Explore family compiles source/self and “target creature you
control” instructions to CardProgram V2. It publicly reveals the current
controller's top card, uses a replacement-aware zone move for a revealed land
or chosen nonland, places the counter only on the same current phased-in
logical incarnation, and emits one typed completion event. Its preparation
continuation pins the exact counter or zone intent, so a replacement choice
cannot repeat the prior reveal. Controller last-known information is captured
when the source leaves the battlefield. Simultaneous multi-permanent Explore,
repeated Explore, Explore replacement effects, and broader granted/copy
propagation remain explicit residuals.

Mandatory source-self combat triggers may also place exactly one +1/+1 counter
after the source deals committed combat damage to a player. The compiler shares
the normalized damage occurrence and fixed event-trigger composition owner;
resolution uses the same incarnation-pinned source reference and canonical
replacement-aware `PlaceCountersIntent` transaction as every other fixed
counter effect. Noncombat damage, permanent recipients, fully prevented
damage, optional placement, other counter kinds or counts, and broader damage
wording remain residual.

One printed fixed positive ordinary-mana or fixed positive life
cumulative-upkeep instance uses a two-stage semantic continuation. The first
stage places its age counter through the same replacement-aware transaction and
can suspend for affected-object ordering. Only after that commit does the
second stage read the permanent's actual age-counter count, calculate the
payment, and issue the controller's payment-or-sacrifice choice. Mana payments
use the canonical mana owner; life payments use the canonical nonreplaceable
life-cost owner. This prevents quantity replacement from changing the counter
result without changing the cost. Resolution rechecks the pinned source
incarnation for the keyword's intervening battlefield condition. A departed or
returned new object makes the ability do nothing; a control change leaves the
original trigger controller responsible for the payment and permits sacrifice
only while that player still controls the permanent. Alternative, snow,
hybrid, Phyrexian, zero, variable, compound, other nonmana, copied, granted,
and multiple-instance forms remain precise residuals.

The compiler also owns one bounded mandatory casting-cost family: “As an
additional cost to cast this spell, put [fixed number] [counter kind]
counter(s) on a creature you control,” followed by exactly one represented
instant or sorcery result clause. The entire two-clause spell lowers to one
source-spanned CardProgram node with an immutable cost descriptor. Cast offers
query current effective creature characteristics and expose only the caster's
public, phased-in candidates. Commit revalidates that same predicate, marks the
placement as a cost rather than an effect, and routes it through the canonical
counter replacement transaction before stack placement. A replacement-order
choice suspends and resumes the complete cast atomically; mana, counters, and
stack state remain unchanged while the choice is pending. Unsupported cost
grammar blocks the whole spell so a following result clause cannot be compiled
as a cost-free action.

The following producers and wordings remain deliberately outside this slice:

- Saga progression with copied, granted, removed, or layer-modified Read Ahead
  or chapter abilities, noncontiguous chapter declarations, and arbitrary lore
  movement;
- negative loyalty counter-removal costs and player-counter removal;
- optional, variable, alternate, compound, multiple, noncreature, and
  non-counter casting costs outside the bounded fixed creature-counter family;
- cumulative-upkeep forms outside the fixed positive ordinary-mana and fixed
  positive life families;
- Support X or zero and conditional, optional, repeated, copied, granted,
  modal, or compound Support instructions, plus variable, distributed, dynamic,
  subtype-qualified, combat-qualified, modal, conditional, compound, and
  multiple-counter target-set clauses, plus fixed player-counter variants
  outside the closed relations;
- Amass X or zero and conditional, optional, repeated, copied, granted, modal,
  compound, linked-result, or non-subtype forms, plus unrepresented token
  prohibition and replacement families;
- conditional targets and non-creature subtype predicates;
- attachment-relative players, cards outside the battlefield, dynamic or
  compound attached-object predicates, and attachment creation or movement;
- Fabricate counter choices now suspend and resume through the typed semantic-completion continuation, while zero, variable, copied, and granted Fabricate variants remain explicit compiler residuals;
- optional, variable, state-derived, copied, face-down, or dynamically
  characteristic-modified Planeswalker and Battle token entry remains outside
  the represented prospective-token boundary;
- unsupported Battle subtype protector procedures and unrepresented
  copy-layer, face-down, or dynamic entry-characteristic interactions;
- counter-removal costs, counter movement, player-counter removal,
  unrepresented rule-generated removals, and effect variants outside the
  mandatory fixed named and direct all-counter permanent families, including
  card-specific continuation paths such as Demonic Junker.

Several of those operations occur inside a larger semantic continuation after
earlier instructions have already mutated state. Routing them through a choice
that can suspend would replay prior side effects unless the enclosing
instruction first gains a typed resumable frame. They are recorded blockers,
not silently approximated migrations.

The component also excludes fractional or halving replacements, dynamic
quantities, counter movement, prevention, other enters-with-counter wordings,
and universal placing-player derivation. Broad CR 122/614/616 stays blocked
until those families and producers are integrated.

Primary assurance is in `test_counter_placement_replacements.py`,
`test_proliferate_rules.py`, `test_proliferate_compiler.py`, and
`test_fixed_counter_placement_effects.py`, with normalized fixed counter-event
trigger compiler, APNAP, replacement, privacy, rollback, and replay evidence
in `test_fixed_counter_event_triggers.py`, and affected- and target-set
coverage in `test_fixed_counter_placement_sets.py` and
`test_fixed_counter_placement_target_sets.py`, plus intrinsic entry coverage
in `test_intrinsic_entry_counters.py`, Support coverage in
`test_support_counter_placement.py`, Amass compiler, staged replacement,
choice, subtype-duration, privacy, rollback, and replay coverage in
`test_amass_rules.py`, attachment-relative result coverage in
`test_attached_counter_placement.py`, shared event-order coverage in
`test_replacement_event_tree.py`, and focused mutation evidence in
`test_capability_implementation_mutations.py`. Fixed named and direct
all-counter removal compiler, target, partial-result, strict result-shape,
rollback, Siege, multiplayer replay, and mutation evidence is isolated in
`test_fixed_counter_removal_effects.py`.
Ordinary Station compiler, cost, timing, resolution-characteristic, departure
LKI, source-incarnation, replacement, rollback, multiplayer privacy, replay,
and mutation evidence is isolated in `test_station_rules.py`.
Damage-result placement,
removal, no-rediscovery, rollback, and focused owner-mutation evidence is in
`test_damage_result_events.py`; the standalone exact-removal transaction is in
`test_rule_generated_counter_removals.py`. Mentor compiler, targeting,
last-known-information, replacement, rollback, multiplayer, and exact-replay
evidence is isolated in `test_mentor_rules.py`. Dethrone and Training snapshot,
qualification, source-identity, replacement, ordering, privacy, mutation, and
exact-replay evidence is isolated in `test_attack_counter_triggers.py`.
Renown compiler, normalized damage, redirection, intervening-condition,
designation, rollback, multiplayer privacy, and exact-replay evidence is
isolated in `test_renown_rules.py`.
Modular compiler, entry replacement, departure LKI, target revalidation,
controller scoping, multiplayer privacy, and exact-replay evidence is isolated
in `test_modular_rules.py`.
Fixed self maximum model, compiler, current-characteristic, simultaneous
removal, four-player replay, rollback, and mutation evidence is isolated in
`test_maximum_counter_state_action.py`.
Target-threaded counter and
characteristic sequences, strict residuals, replacement suspension, rollback,
four-player privacy, exact replay, keyword-counter projection, and focused
mutation evidence are isolated in `test_fixed_target_effect_sequences.py`.

Current aggregate corpus counts and remaining blockers are generated in
[`docs/COMPILER_COVERAGE_STATUS.md`](../COMPILER_COVERAGE_STATUS.md). They
measure represented behavior against the pinned corpus, not matchup results or
complete Oracle correctness.
