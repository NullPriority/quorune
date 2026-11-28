---
title: "Target legality and protection"
status: "current"
authoritative_source: "quorune/targets.py, quorune/target_characteristics.py, quorune/target_history.py, quorune/target_numeric.py, quorune/object_query.py, quorune/target_predicates.py, quorune/protection.py, quorune/damage_source.py, quorune/target_protection.py, quorune/target_protection_engine_adapter.py, quorune/target_source.py, quorune/target_restrictions.py, and CommanderEngine._target_row_matches"
verified: "2026-10-21"
audience: "rules, compiler, replay, and architecture contributors"
maintenance: "hand-maintained"
---

# Target legality and protection

A source Equipment entry trigger can require a legendary creature controlled
by the trigger's controller. Placement requires one legal target and skips
the trigger when none exists. The shared target query checks the creature type,
Legendary supertype, controller, and original battlefield incarnation again
on resolution. The ordinary attachment owner then moves the source Equipment
without an equip activation or payment.

Version-four typed Enchant represents a modified creature, a nonbasic land,
or a creature with another Aura attached. The modified predicate uses the
canonical attachment-state query, including the permanent controller's Auras,
Equipment under any controller, and counters. The nonbasic predicate checks
the Basic supertype.

The other-Aura predicate requires a distinct phased-in Aura with a current
reciprocal link to the creature. It uses effective types and accepts an Aura
under any controller. Cast offers, commands, resolution, untargeted Aura entry
and ongoing attachment legality share the same target owner. Simultaneous
entry chooses recipients against the pre-batch battlefield. When the last
other Aura stops enchanting the creature, the usual state-based action puts
this Aura into its owner's graveyard.
The capability is inferred from the exact typed fragment. Earlier Enchant
encodings use their original payloads for replay.

A linked graveyard Aura changes its legal attachment domain when its
trigger returns the enchanted card. The return uses the original ability
controller and the committed battlefield incarnation. The ordinary Aura
owner checks the untargeted attachment and protection. Its delayed
sacrifice follows that incarnation, even if protection prevents attachment,
and triggers when the original Aura departs. Earlier reviewed Game Record
v3 effects retain their stored physical-object encoding.

An announced X can bound each distinct mana value in a group of creature cards
from the caster's graveyard. The group permits a zero-target declaration.
The maximum is X plus one before the offer narrows it to the number of
represented values. Selection uses the effective public graveyard front face,
and resolution compares each surviving target with its original incarnation
and value. The concrete group is retained with the stack item's existing
target schema. The exact distinct-value return pairs it with one simultaneous
battlefield entry for the legal survivors. The result receives the count of
originally selected targets and uses the existing movement and replacement
owners. The return seals the printed creature front of a modal double-faced
card before the entry owner measures replacement effects. The fixed-count
reanimation instruction retains its established encoding and behavior.

The target-language owner relation `spell_owner` reads the acting spell's explicit
owner, independently of the spell controller and target controller. Live spell
copies use their existing noncard object ownership; unavailable ownership rejects
instead of becoming the current controller. The fact is ephemeral and leaves the
version-one serialized ability-source binding unchanged. Runtime dependency
inference requires `target.object.same_spell_owner` for this relation even when
annotations omit it. Human and Changeling targets remain excluded from Mutate.
Changing a spell's controller preserves its owner. A copy receives its own owner
from the player under whose control it is created.

The targeted return of an opposing spell or nonland permanent uses one
target slot. The same opponent relation applies on the stack and on the
battlefield. A stack target resolves to its current spell card and leaves
by the ordinary owner-hand zone transition, including when the spell is
uncounterable. A moved spell copy ceases to exist through state-based
actions. Invalid or departed targets follow ordinary target revalidation.

A counter that protects its controller or their creature can choose a spell
or ability with at least one matching current target. The stack target reader
follows the announced battlefield incarnation and the player's current game
membership. A creature that stays under the controller's control and gains
hexproof still qualifies. A departed incarnation leaves the target set used
by the counter predicate, and a returned card receives a fresh identity.

A qualified copy can target an instant spell, sorcery spell, activated ability,
or triggered ability under any controller. One shared public predicate
distinguishes spell types from ability kinds both when offering and revalidating
the target. The copied stack item retains its existing source and paid choices.
For fixed Spree, this mode can accompany a distinct single-target change mode.
Each announced target is revalidated separately, and the copy instruction runs
before that change in printed order.

A spell that copies and returns one targeted instant or sorcery uses the
same revalidated stack object for both instructions. The existing copy
choice may retain the original targets or announce legal replacements.
After placing the copy on the stack, the resolution moves the original
spell to its owner's hand through ordinary zone movement. The move uses
the original spell's owner when someone else controls it, and applies to
uncounterable spells. The resolving player controls the copy. Cast history
records actual casts only.

A spell that copies and returns one targeted instant or sorcery uses the
same revalidated stack object for both instructions. The existing copy
choice may retain the original targets or announce legal replacements.
After placing the copy on the stack, the resolution moves the original
spell to its owner's hand through ordinary zone movement. The move uses
the original spell's owner when someone else controls it, and applies to
uncounterable spells. The resolving player controls the copy. Cast history
records actual casts only.

Intrinsic Mutate cost metadata uses the existing ability-fragment registry and
the shared fixed ordinary/hybrid mana expansion. Its copied fragment contains
only the closed mana-cost value and version, not an Oracle line for runtime
parsing. `rules/casting/mutate_costs.py` binds a complete, current face before
adding typed alternatives to the ordinary cost pipeline. The chosen cost pins
its target schema and payment marker into the existing stack context.
`casting.mutate.fixed_mana` owns the declared alternative, target, physical merge
and ordinary-creature fallback. Copies retain the declared alternative and use
the existing copy object's owner when their targets are revalidated. Every other
material ability still requires complete-card admission; metadata alone does not
admit an otherwise partial original.

Quorune uses one server-side target predicate for offer generation, submitted
command validation, and resolution-time revalidation. A client receives only
the legal public references in its current action schema and cannot substitute
an authoritative object identifier or bypass the same predicate at commit.

An active opposing Flagbearer ability constrains the complete set of targets
chosen for a spell or activated ability. The engine first checks ordinary
target legality, including protection. It uses the chosen modes and number of
targets to determine whether a complete assignment can include a Flagbearer.
Any legal battlefield Flagbearer can satisfy the requirement. The target-cost
owners preserve feasible Flagbearer assignments when they compress equivalent
prices. The browser applies requirements for the selected modes and target
counts. A player who chooses zero optional targets can still cast the spell.

A spell with one printed X can use it as the mana-value maximum for a
mandatory battlefield target. The casting offer gives each target an X range
using its current mana value and payable price. Target-specific taxes, selected
Splice costs, and hand-exile credit all feed the existing price owner. Casting
pins the chosen X into the ordinary target schema, and a copy retains that
bound when choosing a new target. Resolution checks the current target's mana
value against the pinned limit, even when a permanent stays in place and turns
face up.

The own-graveyard version applies the announced mana-value maximum to one
creature card the caster owns. The spell preserves that ownership predicate
when it announces targets and when a copy selects a replacement target.
Resolution runs the ordinary reanimation instruction after revalidating the
target. An announced X of zero remains a legal ceiling.

A sorcery-speed activation can bind the same upper bound to one artifact or
enchantment card in its controller's graveyard. The activation pays its
fixed colored mana and announced X and returns one controlled land to its
owner's hand. Payable offers show the target's minimum X and the largest
payable X. The announced X and target incarnation are retained for resolution.

An X-cost permanent's self-entry trigger can limit one owned artifact or
creature card to its sealed entry X. Trigger creation binds the ceiling
before target selection. Entry without a resolving spell supplies zero.
The ordinary public target selector checks ownership and current mana value,
and the trigger retains its original bound if its source leaves.

An announced-X owned graveyard return uses an untargeted versioned
public set for creatures and planeswalkers. Its declared limit is separate
from the fixed query's mana-value field, so the resolved X can exceed the
literal query grammar's range. The set resolves through the ordinary
simultaneous battlefield entry owner. Copies use their own controller's
graveyard and the copied X.

A tap activation that grants a cast for a targeted artifact checks the
ability controller's own graveyard and retains the selected incarnation
through ordinary revalidation. An artifact land is a legal target. The
resolved grant authorizes only casting the selected card this turn with
ordinary timing and payment. Leaving and returning to the graveyard
creates a new object outside that permission.

An own-graveyard historic target can be an artifact, a legendary card, or a
Saga. The mixed-characteristic target owner applies that disjunction to its
one physical card, including a legendary land or a Saga land. Action offers
and resolution both verify the same ownership and current incarnation.

The spell that phases out up to X target creatures uses the ordinary
announced cardinality. The caster can choose fewer than X targets. If Splice
adds spell text, the offer keeps the selected base price and applies the
cardinality only to the original phasing group. A copy retains the paid X and
the number of targets originally chosen. Resolution uses currently legal
targets and phases them out together, including their indirect attachments.
The next untap step of each recorded controller uses the ordinary phase-in
plan.

Any-number phasing uses that same simultaneous status change. An instant or
sorcery can choose controlled nonland permanents. The defined self-entry
trigger chooses other creatures its controller controls and excludes the
entering source. Both scopes reuse ordinary target announcement and
resolution-time revalidation. The set limit records the originally chosen
count so later illegal targets can drop out without adding replacements.

The shared compiler grammar accepts the control qualifier before or after one
supported `with`/`without` keyword qualifier. Both orders lower to the same typed
conjunction. Offers, submitted targets and resolution revalidation require both
current control and the current keyword fact; unknown or compound keyword tails
remain outside the closed grammar.

Printed Protection lists with repeated `from` clauses compile to independent
typed qualities, including comma-separated color and creature-subtype lists.
Keyword recognition, fragment lowering, and dependency checks share the same
printed keyword boundaries. Sibling keywords retain their lowering and capabilities;
unknown qualities and ambiguous continuations remain residual. Targeting,
attachments, damage prevention, and blocking consume the existing Protection
fragments without interpreting the list at runtime.

`selection/target_query.py` resolves an explicit object ID or case-insensitive
public reference before name lookup. A matching reference keeps its identity
even when another object's name matches that string. Zone, owner, controller
and outside-zone filters still apply; a rejected reference does not fall back
to another object's name. Without a reference match, the existing printed and
effective name query retains ambiguity checks. Reference lookup does not
evaluate unrelated objects' continuous characteristics or keep a state cache.

A direct token target checks token status independently of card type. A printed
creature or artifact qualifier adds that type test. Controller, tapped status,
and entry during the current turn use the same public predicates at selection
and resolution.
The same predicate can exclude tokens from a represented permanent domain.
Nontoken type disjunctions exclude tokens from every branch; nonland and
nontoken qualifiers remain independent requirements. Copying a card does not
make a token into a nontoken, and copying a token does not change a card's
nontoken identity. The compiler emits a strict Boolean token field and retains
all other controller, source-exclusion, type and timing restrictions.

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

An Aura's activated instruction to exile its enchanted creature is untargeted,
even though casting the Aura itself requires a target. Shroud and Hexproof do
not stop that activated instruction. Its versioned attachment reference uses
the current attachment of the original Aura incarnation, or its last-known
attachment after departure. It retains the referenced permanent through type
changes and rejects a returned recipient's new incarnation. No target schema
or resolution-time creature target predicate is synthesized for this form.

A mandatory change to a single-target spell can carry a separate life-loss
instruction for the resolving controller. The change uses the original
spell's target authority. The life loss reads the original spell's current
stack mana value, including announced X, after the target change or after
the game finds no legal alternative. The pending choice keeps the following
instruction in the existing resolution continuation. An illegal original
spell target prevents both instructions from resolving.

Closed stack-counter predicates keep disjunction branches independent. A
triggered ability or colorless spell does not include an activated ability
merely because its source is colorless. An activated or triggered ability may
qualify independently of the source's type or legendary status when those
restrictions apply only to the spell branch. A blue instant spell must satisfy
both properties on that same spell. Spell copies use their own spell
characteristics. The shared predicate controls legal offers, submitted targets
and resolution revalidation; the existing stack-counter owner performs the
actual countering. Ability-source type restrictions retain their separately
bounded current/last-known-information contract.

The closed artifact-source activated-ability and noncreature-source ability
filters read `TargetingSourceBinding`, including for abilities without targets.
Current games announce nonmana activations before paying source-sacrifice
costs. Trigger discovery captures the event source, and canonical departure
capture pins the original incarnation's last characteristics. A returned card's
printed types cannot replace that information. Missing source facts cannot
prove that a source is noncreature. These predicates require
`target.ability_source.current_or_lki` in addition to ordinary countering and
target revalidation authority; historical absent-version frames retain their
earlier capture behavior.

An optional target set may begin with zero chosen objects (CR 115.6). After
validating its descriptor, the homogeneous destruction, movement and tap-state
handler emits the existing empty result plan without object mutation. Later
independent instructions still resolve. This differs from choosing targets
that all become illegal before resolution, where CR 608.2b stops the spell or
ability. Nonempty batches retain their simultaneous owner and strict snapshots.

An activation that returns cards from one opposing graveyard before an
independent targeted result declares both groups before payment. The first
group requires distinct cards from a single opponent. The second group
retains its own target predicate and binds its result only to its surviving
target. Resolution returns the surviving graveyard cards simultaneously to
their owner's hand, then applies the second instruction through its existing
owner. A damage-source choice occurs at that second instruction and requires
a surviving prevention recipient.

`targets.py` owns the closed schema vocabulary and public-zone boundary.
Direct permanent targets can combine unqualified card types and subtypes as
flat alternatives, such as creature or Vehicle and Spirit or enchantment.
`DirectPermanentTargetSpec` serializes these through the existing
`TargetCharacteristicForm` and `TargetGroup.characteristic_forms_any` fields.
One permanent satisfying several alternatives remains one target. Common
controller and source-exclusion scope applies to every branch; unknown terms,
nested forms and branch-local qualifiers remain residual. Offers, submitted
commands and resolution use current effective characteristics. Losing one
branch leaves a target legal if it still satisfies another; losing all branches
makes it illegal under the ordinary revalidation owner.
The direct nonartifact, nonblack creature shape requires creature type,
absence of artifact type, and absence of black among the target's colors.
The offered and submitted target use the same predicate. Resolution checks
both exclusions again, including after a type-changing response. Paid casting
costs remain separate from the result's target revalidation.

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
A targeted life charge is fixed with the other casting prices after target
announcement. Repeated instances of the same targeted permanent charge its
source once. Distinct sources combine with the other life costs and use the
ordinary nonreplaceable payment before the spell finishes casting.

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

The paired controller-turn clause grants Hexproof to the player and source
during the source's current controller's turn. Both typed branches carry the
same controller-turn condition. Target legality follows a control change in the
middle of a turn, and independent printed abilities keep their normal timing.

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

Public graveyard card-set queries use the same current mana-value fact through
an explicitly versioned `ObjectQuerySpec` upper bound. The closed form selects
physical cards at fixed bounds from zero through twenty. It does not widen the
battlefield power/toughness predicate or admit a hidden-zone, variable-value,
or receiver-union query. Older query wire shapes retain their exact fields;
only the new card predicate carries `mana_value_max`.

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

For a fixed Choose one spell, the selected mode contributes its complete
grouped target plan. Group-local predicates and exclusions remain separate,
while the selected child's global distinctness and same-player constraints
reach both availability and command validation. Unselected modes contribute
no targets or constraints. Copying keeps the chosen mode and per-group count.

Separate printed occurrences of `target` retain separate groups. The same
object may fill each unqualified occurrence when it meets each predicate;
`another target` in the second of two independent clauses requires distinct
objects. The compiler preserves the source spans and passes that constraint
to the existing spell-composition certificate and target planner. Each scalar
result uses only its own group. An illegal target in one role does not suppress
another legal role, and a copy rebinds each role under the existing per-group
chosen-count rules.

An ordered same-set result program announces targets once. Its `them` or
`those` follow-ups consume the same surviving original target incarnations;
each instruction does not discover a fresh set. A returned card with the same
public reference receives neither instruction for its departed incarnation.
The initial optional empty choice still permits independent following
instructions, while losing all chosen targets stops the spell. Copies use
the existing fixed-count and new-target authority for that same shared set.

Optional one-per-player permanent selections use the existing announced target
count and one simultaneous homogeneous result. `distinct_controllers: true`
requires selected permanents to have different controllers at announcement;
`retain_controller: true` revalidates each selected permanent against its
recorded controller. A change to another opponent's control makes that target
illegal even when it still meets the ordinary opponent predicate. Other legal
targets resolve, and an initially empty selection remains a legal choice.
Both flags are strict booleans and require `target.set.one_per_player`, inferred
from the actual schema independently of capability annotations. Ordinary target
groups omit these flags and retain their existing serialized shape.

Copies retain the original chosen count and snapshots. Keeping all original
targets may retain illegal targets; newly chosen targets satisfy the copy
controller's current domain and the distinct-controller relation. Departed
original incarnations do not become new targets through reference reuse.
Mandatory selections for every player and result bodies without an existing
closed homogeneous owner remain residual.

Copied spells and abilities retain their selected modes and original target
count in each group. `selection/copy_targets.py` binds those decisions to the
captured original target groups and identity snapshots. Storm and generic copy
choices then use the same structural validator in
`selection/target_validation.py`, and genuinely changed targets still use the
ordinary current target predicate and copied spell or ability controller.

A supported power-adjusted die table targets a stack object with at least one
announced target. Resolution uses the signed maximum current power of creatures
controlled by the resolving player. The table offers ordinary reselection for
the original under its current controller. A high result creates a separate
copy choice after reselection, using the changed targets as defaults and the
new copy controller for legality. The saved choice retains that continuation
across reload without another roll.

A retained-index selection keeps the original target incarnation even when it
is no longer legal or has left its expected zone. An explicit current-reference
selection chooses the currently represented object and must be legal. The
client exposes these as separate per-slot options, including when a physical
card has left and reentered under the same public reference. A retained target
never receives the new incarnation's snapshot. Legacy default-reference arrays
remain readable as original-target retention; grouped current-reference
selections distinguish new targets. Ordinary casting and unrelated retargeting
receive no copy retention exemption.

A targeted graveyard return can offer to copy its resolving source after the
original return when the physical spell was cast from a graveyard. Losing the
sole original target stops that resolution before either instruction. The
copy uses the standard target choice, including retention of the original
incarnation after it leaves the graveyard. Return triggers wait until the
copy's target choice is finished, then resolve before the copy. Only the
original recorded graveyard cast carries authority for the optional copy.
[Commander 2019 release notes](https://magic.wizards.com/en/news/feature/commander-2019-edition-release-notes-2019-08-09)

Event-bound spell copies feed the same validator from current or departure-
pinned spell information. Their prospective targeting source uses the copied
characteristics and the copy controller. The original spell is a distinct
object and may be a legal new target of its copy. Without an explicit new-target
permission, the copy retains every original target choice and incarnation.

Normalized becomes-target occurrences use those committed snapshots as well.

A fixed tap activation with one random target samples the current legal target
group during announcement. Its ordinary cost owner then prices the selected
target. A failed payment rolls back the action, and repeating the same proposal
uses the same draw. The offer exposes the activation without a target picker.
A retained departed incarnation creates no occurrence on a returned permanent
with the same public reference. Choosing that returned incarnation explicitly
does create an occurrence; retargeting compares incarnations before suppressing
an unchanged-target announcement. Copies retain their own stack occurrence and
do not publish a cast event.

The fixed two-mana counter trigger can observe an opponent targeting a Sliver
creature controlled by the source's controller. Trigger discovery reads the
recipient's effective creature type and Sliver subtype at the announcement,
including Changeling. Each newly targeted creature has its own occurrence.
The subtype is available to discovery only, so the version-one event envelope
keeps its original fields. The trigger refers to the announcing stack object,
and its current controller receives the payment choice at resolution.

The current representation fails closed before copy commitment if one
assignment would require different incarnations sharing the same public
reference, or if a changed relational assignment requires unavailable current
controller facts for a retained nonplayer target. These are explicit
composition limits, not successful approximations. Current-version replay,
descriptor readability and historical execution remain separate claims; old
records require matching execution provenance and are rejected when that
provenance is incompatible.

A source-controlled blocking prohibition is a declaration rule locked to
the target and the current source incarnation. It uses the original
controller's control and phasing history. A committed loss of continuity
ends the prohibition, even if the original controller regains the same
permanent. A separate clause can still resolve when the prohibition never
starts.

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
or ignore the Aura's Enchant restriction.

Schema v4 represents protection from a publicly chosen player. The linked entry
component binds the choice to the current source incarnation and copy form. A
current battlefield ability materializes the choice in layer 6. Targeting uses
the controller of the spell or ability. Damage, blocking and attachment use
the controller of the relevant object, or its owner when it has no controller.
These checks share the typed protection verdict and attachment legality is
rechecked after control changes.

A same-face entry choice can link one card type to a static grant of Protection
for the source's current controller and creatures that player controls. The
entering controller chooses a card type before entry. The retained choice
follows the current source incarnation and copy form. The ordinary layer-6
query applies to the current creature set, and player targeting, attachment,
and damage consume the same typed Protection quality.

Schema v5 represents one chosen-color Aura grant that retains the current
Aura controller's eligible attachments. A retained Aura or Equipment must
still have a reciprocal attachment to the same creature incarnation. The
relation must have begun before or simultaneously with the granting Aura's
attachment. A simultaneous entry transaction preserves that common boundary
even when layer timestamps differ. Reattachment starts a new relation.
Control changes recheck the live controllers. The exception belongs to that
one protection instance when checking attachment retention. New attachments,
targeting, blocking and damage use ordinary protection.

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
A color-changing instruction for a target artifact creature uses the
conjunctive artifact and creature predicate in both offers and resolution.
The color layer preserves the object's types.

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
An explicitly empty type line represents a source with no types, including an
emblem under CR 114.3; its identity and controller remain sealed. This known
empty value is distinct from missing or malformed characteristic data.
An Encore frame seals its acting graveyard source separately from the card
produced by its exile cost. New Encore payloads carry that copy incarnation for
current-or-last-known copying; older payload forms retain their recorded path.

Shared keyword queries treat a qualified variant as Hexproof. Removing or
prohibiting Hexproof removes every variant; later grants and independent plain
Hexproof follow the ordinary layer-6 owner. Existing typed grants and removal
carry these closed values through the same target-revalidation path. Fixed
player keywords use their separate components described above. Unrepresented
chosen qualities, compound type lists, targeting exceptions and rules-text
equivalents remain material residuals. Runtime code consumes compiled values.

A compound Hexproof clause can cover the current source controller alongside
controlled Humans, or alongside controlled planeswalkers and other controlled
creatures. The compiler joins the existing player and permanent grants in one
static program. The source exclusion belongs to the creature query. The
planeswalker and Human queries can include the source if its effective types
qualify. Offers, commands, and resolution use current target legality.

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

A represented cast-entry trigger can grant its resolving controller protection from everything until the beginning of their next turn. The original cast receipt binds the trigger to that entry incarnation. The typed player protection applies to targeting and preventable damage for that duration. This descriptor leaves life total changes and permanent phasing to their own rules. Historical protection journals retain their recorded descriptor semantics.

An untargeted battlefield activation can grant the same player protection. The
versioned instruction binds the resolving activation and its controller even
after a paid source move. The shared rules owners prevent targeting, Aura
attachment and preventable damage until that player's next turn. Untargeted
life loss and damage that cannot be prevented remain applicable.


A grouped entry trigger can announce one optional creature per active player.
The triggered object seals the roster. Each target stays in its original role
through copies, retargeting and resolution. The destruction owner commits the
still-legal targets in one batch. A qualifying destruction of a creature
controlled by the resolving player requests two counters on the original live
source. Destination replacement and prevention keep their separate outcomes.
The reward uses the ordinary counter placement owner after destruction.

A complete linked graveyard Aura face binds its cast and entry carriers to one
linked Enchant descriptor. The original static Enchant and attached modifier
retain their own source spans. Current registration checks both lifecycle
carriers before superseding the historical reviewed pair.

## Turn-long caster-relative restrictions

A resolving spell can protect one targeted creature from the spell controller’s
opponents until cleanup. The restriction uses that resolving controller and
the target's current incarnation. The ordinary target owner applies it when
offering new targets and revalidating pending instructions. A kicked copy
retains the paid cast fact and applies both instructions to its own target.

## Another legendary noncommander ability source

A controlled stack ability qualifies for this copy target only when the
source's effective supertype is legendary, its commander status is known to
be false, and its original incarnation differs from the copy activation's
source. The target check applies during announcement and revalidation. It
uses current facts while the source exists in its original zone and last
known facts after the source departs or phases out.

Ability-source information version three records the commander fact with the
existing source binding. Earlier records retain the prior shapes and their
existing ability-source predicates. The new predicate requires complete
commander and source identity facts.

The paid battlefield commander return targets a permanent designated as a
commander and checks its actual owner against the activating seat. It uses the
regular priority and current-turn condition, including a nonempty stack. The
target stays eligible under another player's control, and a legendary
lookalike without commander designation stays ineligible. Ordinary target
incarnation revalidation and the existing owner-hand destination replacement
choice govern resolution.

The ordinary Saga's per-player exile chapters offer one optional permanent
for each player, including the source controller. Land and Saga permanents
are excluded. Resolution revalidates the announced controller and target
incarnation before the canonical source-departure exile and return.

## Returning a graveyard card of each color

A per-color graveyard return offers a separate optional group for each of
the five colors. The same physical card cannot fill two groups, while two
multicolored cards can fill different groups. Resolution rechecks each
announced card against the color group originally selected and moves only
the legally retained targets. A terminal self-exile applies when the spell
resolves, including a cast with zero targets.

A homogeneous optional set of controlled permanent targets can receive a fixed
keyword through cleanup. Each distinct announced permanent revalidates before
the grant. Its resolving incarnation retains the granted ability through later
controller changes.

A non-Aura enchantment can use the same linked graveyard lifecycle through its
self-entry ability. The spell has no target. The entry trigger checks the
original battlefield incarnation and the selected graveyard creature before
creating the source-bound Aura type and Enchant restriction. The shared return
owner attaches only to the actual arrival and schedules the same departure
sacrifice. Independent timing permissions remain with the casting owner.
