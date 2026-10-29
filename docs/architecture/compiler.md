---
title: "Oracle compiler architecture"
status: "current"
authoritative_source: "quorune/oracle_ir.py, quorune/compiler, and quorune/card_programs"
verified: "2026-09-29"
audience: "compiler and rules contributors"
maintenance: "hand-maintained"
---

# Oracle compiler architecture

Attached-source casting restrictions opt in through version two of the existing
player-restriction descriptor. The source-turn opponent rule reads a current
reciprocal attachment to a battlefield creature, including its effective type;
it does not capture an equip target or add an activation prohibition. Version
one keeps its exact fields and behavior. Equipment controller, owner and
equipped-creature controller remain distinct inputs to their existing owners.

Fixed sacrifice-price spell promotion validates the canonical typed price and
its declared authority separately from the independently closed result. The
composed producer prefix is checked against that price before testing the
result through existing closed-effect owners. A strict capability binding alone
does not promote unknown prices, malformed descriptors or unrepresented results.

The static dispatcher first gives the complete source to its existing exact
owners, preserving supported Domain, Max speed, Raid and timing conditions.
Only when no exact owner recognizes that source does it remove a leading
nonfunctional ability or flavor label
before invoking its existing typed body owners. Declaration nodes keep the
original labeled text and source span while parsing that complete material
body. Numeric labels and their punctuation also precede ordinary triggered
abilities. The same normalization applies to synthesized static abilities.
Functional visit instructions remain outside this normalization;
every condition, threshold and result in a body must still lower exactly.

`compiler/live_modifier_quantities.py` lowers represented live player values,
attached-recipient colors and mana value, attachment unions, and public-set
power/toughness modifiers. It uses the existing source, attachment, public
query, and layer owners. Characteristic quantity version four reserves closed
nonnegative life, opponent-poison, and attached-object references; earlier
quantity encodings retain their fields. These new references do not define
characteristic values in layer seven a.

The quantified anthem production validates its creature subject through the
existing fixed-query anthem owner. An “other” quantity proves that every
affected recipient belongs to the counted set before excluding one member.
It continues to count correctly when the ability source is outside that set.
Fixed keyword grants remain separate components, so a zero numeric quantity
does not remove an unconditional keyword. Ambiguous attachment pronouns,
private identity predicates and later-layer characteristic reads remain
residual.

`compiler/fuse_faces.py` binds the printed Fuse declarations across both faces
of a split card after their ordinary spell bodies lower. The existing ability
component registry validates one complete-source declaration. Neither an
isolated Fuse keyword nor an exact half admits an unsupported companion.
`compiler/fuse_programs.py` derives the fused resolution program from the two
ordinary programs, retaining their source identities and capability closures.
Its shape owner checks both original bodies, their order, and the exact combined
effect and target projection. Missing executable composition remains a material
CardProgram residual; the physical card still has two faces.

`compiler/adventure_faces.py` binds each Adventure alternative's existing
source-spanned nodes to `casting.lifecycle.adventure`. Runtime requirements
retain that intrinsic authority even if a program omits its explicit
dependency. Ordinary whole-card admission still rejects unsupported bodies or
normal-face siblings. Current versioned casting offers the alternative using
its own characteristics and price. The `adventure.py` owner carries its sealed
face binding through ordinary resolution, substitutes exile for the resolving
spell's graveyard destination, and grants only the resolving controller a
normal-face permission for that exile incarnation. The ordinary zone, casting,
cost, target and stack owners perform the transitions. Countered and
all-targets-illegal alternatives gain no permission, and a spell copy grants
none. An absent execution marker retains historical behavior as described by
the [Game Record contract](../reference/game-record.md).

Each represented half keeps its own fixed single-object target roles. The
composer maps flat and named references into distinct canonical target groups,
preserving constraints within a half while allowing the same object in both
halves. A target-hand-count amount receives an explicit version-7 group binding;
its player remains independent of the other half's target. The runtime minimum
validator restores each half and delegates its requirements to the existing
owners. Variable printed mana, modal or variable-cardinality target bodies,
additional-price declarations, and unowned companion instructions remain
residual under this production. Earlier scalar encodings retain their fields
and meaning.

`compiler/action_permission_templates.py` lowers the complete optional
opening-hand battlefield clause to a hand-scoped permission in the existing
action registry. Named self-references and ordinary object pronouns use the
shared compiler reference owner. The descriptor requires the existing
complete-card admission certificate: an independently trusted fragment cannot
authorize entry when material siblings remain unclosed. The separate
`compiler/opening_hand_reveal_templates.py` production uses that same admission
boundary for optional opening reveals. It closes the complete clause around
one represented result and a first-global-upkeep, first-own-upkeep or
first-own-main timing. Result lowering happens at compilation through the
existing fixed Scry, token, mana, actual-life-transfer and library-selection
owners. Paid reveals, unknown results and unrepresented timing remain residual.

The existing binary additional-cost production also accepts a canonical
creature-subtype card revealed from the caster's hand. The reveal leaf uses
the same typed alternative-price schema as the fixed mana branch, for both
permanents and instant/sorcery results. Optional linked cast-fact reveals,
Behold and unknown qualifiers remain residual. Complete result admission stays
independent of recognizing the casting price.

Fixed additional tap prices compile a positive count and a closed union of
controlled permanent types. The existing alternative-price production may
pair one with a fixed mana payment. The separate
`compiler/additional_cost_payment_faces.py` composer preserves independently
lowered literal Convoke, Improvise and Delve nodes beside an admitted price and
one complete result. Each node retains its original source span, including
payment keywords between the price and result. Repeated instances retain their
ordinary redundancy. Concurrent Convoke and Improvise still require a mixed
tap-payment owner and remain residual, as do variable tap discounts and unknown
price or keyword qualifiers.

Fixed Waterbend costs compile into an optional typed activation cost field or
one mandatory casting-price descriptor. The existing result owners keep their
source spans and capabilities. A separate price/result composition capability
validates the complete Waterbend price and the independently admitted result;
recognizing the price does not admit an unknown result. Printed Waterbend
action observers bind the acting player through the normalized public-action
event owner. Optional and variable Waterbend grammar remains residual.

`compiler/optional_life_costs.py` admits the complete declaration that optionally
pays two life to reduce one corresponding colored mana for a matching colored
permanent spell. The declaration includes its colored-only restriction and
requires complete source admission. Current battlefield abilities supply the
choices; the existing total-cost and life-payment owners apply the price.
Each provider can be paid once per spell, including when its reduction is zero.
Generic and other-color costs remain intact. Unknown amounts, mismatched colors
and incomplete declarations retain material residuals.

`compiler/targeted_spell_costs.py` lowers opponent spell-cost increases for the
source itself and one or more controlled commander creatures. Descriptors bind
the fixed generic amount and target subject; the existing cast-cost component
registry and current static-ability fragments own runtime applicability.
Version-three descriptors also represent fixed generic battlefield-source
reductions for spells targeting the source or at least one current creature.
They retain an explicit all-caster, source-controller or source-opponent
relation. A qualifying target set applies the adjustment once per source;
per-target cardinality, granted target-cost abilities, activation prices and
colored or variable adjustments remain outside this production. Historical
tax and intrinsic source-face predicate schemas keep their existing wire.

Version four composes a current source, attachment or fixed public target query
with a typed spell-characteristic predicate and caster relation. The shared
target-subject compiler serves both spell and activation costs. Aura reductions
read the proposed spell's selected face through the canonical face view, so a
back-face Aura uses its Aura characteristics while its front face remains a
creature elsewhere. Public subject queries keep controller and owner roles
separate and count each source adjustment once across the announced target set.
The existing cost owner applies increases before reductions and preserves
colored payments. Hidden, temporal and numerical target predicates remain
residuals. Recipient-granted cost instances use the quoted-ability path below.

Version five represents a fixed generic adjustment per distinct selected target
or distinct targeted battlefield creature, with an explicit caster relation
and selected-spell predicate. Repeated references to one object count once for
this family. The source-local Strive/per-extra-target producer remains separate.
Count-aware witnesses retain each signed adjustment and supported target/X
range through the existing cost owner. Historical modifier versions retain
their exact payloads; the new fact version distinguishes count-sensitive
quotes without changing their source provenance fields.

`compiler/targeted_activation_costs.py` composes the existing activation source
selector with a current target subject: the modifier source, its attached
permanent, or a fixed public characteristic query. A separate version-two
handler requires the target-dependent capability; the historical flat handler
rejects that version. Equip discounts use the canonical ability-kind selector,
and an Equipment's own conditional equip discount retains its provider identity.
Shared target witnesses drive both affordable target projection and the exact
accepted price. Generic reductions preserve colored and nonmana obligations.
Variable adjustment amounts and hidden, temporal or numerical target predicates
remain residuals. This production does not lower granted spell-tax abilities.

`compiler/preparation_nodes.py` binds an enters-prepared clause on the normal
face of a preparation card to closed inset instant or sorcery characteristics.
The handler requires complete-card admission, including the inset result.
Current static-ability presence controls the entry replacement; alternative
characteristics remain copiable independently of later ability removal.
An inset copy has only its inset characteristics and does not acquire another
prepare spell. Individual preparation names remain selectable by the existing
card-name choice owner; the database's combined display name is not a name
choice. Unknown preparation clauses and unsupported inset types stay residual.

`compiler/conditional_flash_nodes.py` lowers complete conditional self-timing
clauses against represented public-state predicates. An “as though” clause
grants timing permission without granting the Flash keyword. An explicit
conditional “has flash” clause additionally emits separate battlefield and
stack characteristic programs. Same-line contributions retain distinct stable
program identities and their complete original source span. Unrepresented
source-attribute conditions, compound numeric timing conditions and cyclic
keyword conditions remain residual.

`compiler/classic_flash.py` lowers the closed intrinsic as-though-Flash clause
with its next-cleanup sacrifice. `rules/classic_flash.py` keeps the selected
timing permission separate from mana and target choices. The optional casting
form distinguishes an independent timing permission from this intrinsic one;
the default uses an independent permission when available. Ordinary sorcery
timing and scoped casts during resolution do not consume the intrinsic route.
No Flash keyword is granted.

An intrinsic use creates the delayed ability at accepted cast, retaining the
cast controller and the canonical cast incarnation. The existing stack-to-
battlefield identity continuation lets the cleanup instruction find the
resulting permanent, while countering, recasting or blinking prevents a later
incarnation from matching. Countering the spell leaves a once-only cleanup
trigger with no sacrifice result. A permanent copy does not repeat the accepted
cast action. The canonical cleanup scheduler performs normal cleanup actions
before placing the delayed ability; the linked zone owner then records a
sacrifice by the permanent's current controller. Other follow-up durations,
conditional predicates and independently unowned sibling effects remain
residuals.

`compiler/cast_cost_modifier_templates.py` lowers fixed ordinary mana bundles
for a carrying spell's self-reduction, including separate generic and colored
components. Its public queries cover current land subtypes, fixed own-graveyard
card thresholds and conjunctive card-type exclusions. New graveyard quantities
explicitly exclude tokens; historical serialized predicates retain their
existing decoder. Counted graveyard condition captures normalize subtype and
card-type whitespace before constructing the same immutable query values.
`compiler/undaunted_nodes.py` lowers each printed Undaunted instance through
that owner with an active-opponent quantity. Repeated instances contribute
independent terms; reminder text stays in the original source span. Unknown
keyword modifications and independently unsupported spell bodies remain residual.

`compiler/cleave_spell_faces.py` derives normal and bracket-removed forms,
including ordinary ordered body paragraphs. It shares paired-program assembly
with Overload while preserving each form's target schema. Balanced source
brackets, both complete leaf closures and distinct body fingerprints are
required before either program is admitted. An unrepresented source or sibling
keeps the whole spell residual.

`compiler/overload_spell_faces.py` derives and validates both fixed Overload
bodies at the whole-spell boundary. The normal physical spell and its separate
target-free derived program retain the original source identity and spans.
Generated-program lowering preserves their distinct identities; the adapter
requires the derived program instead of silently admitting only the normal
body. A declined pair keeps the keyword residual.

Current source-pinned, trusted historical Overload programs retain their
reviewed declared alternatives through the existing cost owner. This
compatibility path requires the original reviewed program and target-free
alternative; it does not admit incomplete generated pairs or add typed support.

`compiler/fixed_stack_spell_copy.py` lowers the closed instant/sorcery copy
clause, including its optional current-controller restriction. The activated
effect gate delegates this same shape to capability validation. Parent cards
with unclosed siblings retain their residuals and cannot execute the new copy
program through strict source validation.

`compiler/casting_keyword_nodes.py` owns the ordered dispatch of closed
casting-cost keyword productions. It retains the existing lowering order and
passes source-record context only to productions that require it. Ordinary
Conspire uses this path to bind one complete printed tap-cost and copy-trigger
declaration. The existing keyword entry points remain available; mutation
witnesses patch the dispatch owner's actual lowerers.

Public-quantity mana keeps its existing typed source-excluding count producer
before consulting the newer live resolution count fallback. Existing optional
source counters on admitted sacrifice and discard subscriptions keep the
ordinary put-or-decline choice; that composition does not admit optional bodies
for independently unclosed public-event families.

Closed “Whenever you cycle or discard a card” wording lowers to the existing
discard occurrence. Cycling pays a discard cost, so this subscription triggers
once for that card rather than independently subscribing to both events. The
“another card” form also excludes the observer. Existing controller predicates,
current battlefield ability presence, APNAP placement and independently closed
result bodies retain their ordinary owners. Source-directed results bind the
triggering source's incarnation, so departure cannot retarget a returned object.
Other event unions remain residual.

Fixed positive Rampage values lower to typed combat-trigger fragments, preserving
each printed instance. The existing block-transition and continuous-effect
owners execute the trigger; variable values and unrepresented sibling abilities
remain residual. The bonus samples current blockers at resolution.

Source-damage death subscriptions combine a normalized creature death with the
existing committed current-turn damage journal. The condition matches both the
damage source's logical incarnation and the departing creature's sealed prior
incarnation. Repeated damage still creates one occurrence for that death;
another source, a returned object, zero damage, or an unavailable journal cannot
substitute. Existing source counter and optional draw owners execute the result,
including ordinary death look-back and principal-correct replay.

Intrinsic Exploit lowers to one entry trigger with an optional creature
sacrifice. The separately printed “When this creature exploits a creature”
ability subscribes to the committed source-incarnation occurrence and composes
its existing typed result grammar. Their source spans and program identities
remain separate, including when the resulting ability targets a player.

Self stat modifiers can reuse complete collection values through the existing
query-characteristic owner. Explicit where-X forms carry a signed coefficient
for each affected stat. A printed “for each … and each …” form carries two
independent quantities, while an artifact-or-enchantment card predicate counts
one card once. All represented queries read the existing layer-5 snapshot;
the modifier updates in layer 7c as zone membership and source control change.
Hidden characteristic predicates, linked result values and unrepresented
collections remain residuals.

Fixed Goad instructions reuse the canonical designation and combat requirement
owners. Historical single-target descriptors retain their payloads. Expanded
single creature targets and closed public creature sets use an explicit version
that selects the current phased-in set at resolution. Target-player sets retain
the selected player's identity. A later bound clause may goad the existing
creature target while preserving its original target schema and incarnation.
Optional fixed Goad uses the ordinary apply-or-decline continuation. The
goader remains the resolving controller, and source departure does not shorten
the designation's next-turn duration. Continuous “is goaded” text, linked result
sets, variable target cardinalities and other durations remain residuals.

Fixed Connive accepts a complete single-object instruction with a represented
integer count. Explicit self references require a permanent source; source
pronouns require a closed source event binding. Target creature instructions
retain the canonical target and bind its current controller at resolution.
The precise Connive shape excludes internal continuation fields, open result
tails and ambiguous event-object pronouns. Its capability closes through the
existing draw, typed discard, destination replacement and counter owners.

`compiler/fixed_resolution_mana.py` lowers complete fixed symbol bundles into
the existing mana effect, one instruction per represented color or colorless
quantity. A represented spending restriction remains attached to every unit.
Spell, entry-trigger and loyalty-ability classification retain their normal
stack timing and separately compiled costs. Fixed positive outputs of any color
use a closed version-two instruction in the existing mana chooser. One-color
outputs select exactly one of the five colors; combinations submit an exact
nonnegative allocation. The existing mana-bundle form exposes that allocation
without enumerating every possible vector. The payload carries an independent
runtime capability minimum and binds the resolving controller and source.
Ordered result composition resumes the remaining instructions once after the
choice. Genuine mana abilities retain their immediate activation owner.
Variable or restricted color choices, unknown conditional replacements, linked
effects and unsupported spending clauses remain residuals.

`compiler/player_speed_nodes.py` lowers Start Your Engines to a current trusted
static marker. Max Speed fixed characteristic bodies use the public-state
condition owner; `compiler/max_speed_nodes.py` independently compiles other
closed ability bodies under the existing parent/child component scope. The
scope follows current controller speed across zones. Unknown bodies remain
residuals, and the Max Speed prefix is retained as a condition rather than
discarded as decorative text.
The compiler-pinned activation catalog also applies those parent scopes on the
battlefield through `rules/scoped_activated_abilities.py`. It uses the same
current source designations as off-battlefield catalogs, so an absent Max Speed
child cannot advertise an activation through its intrinsic descriptor.

`compiler/backup_nodes.py` binds each fixed Backup instance to the complete
printed ability bundle below it after the face's ordinary nodes are compiled.
Keywords and typed declaration fragments retain their owners; activated and
triggered abilities use independent granted child programs. Unsupported
downstream text keeps the complete Backup production residual. Copies entering
with Backup retain that printed ordering; additional gained abilities do not
change its compiled bundle. Separate instances retain separate trigger IDs.

`compiler/player_target_protection_nodes.py` lowers only fixed "you have
hexproof" and "you have shroud" lines to trusted source components. The
existing ability-component registry validates the closed keyword descriptor;
the target-protection owner derives the current controller's player grant.
Conditional or qualified grants and unsupported card siblings remain residual.

Closed one-or-more public entry and departure subjects reuse the singular
typed characteristic query grammar. The compiler marks their generated
trigger for one occurrence per committed simultaneous batch, scoped to source
incarnation and ability identity. Separate batches remain separate events.
The once-each-turn descriptor independently limits qualifying entry triggers.
Departure subscriptions, including source and external sacrifice events, remain
outside that descriptor's admitted scope. The compiler, semantic shape validator
and runtime trust binder share the same exclusion; an independently supported
unrestricted sacrifice observer is unaffected.
Result references that require the entire matching object set remain
residuals, as do unsupported sibling abilities.

The compiler transforms a pinned local card and rulings snapshot into typed
Oracle IR, recognized semantic nodes, dependency declarations, material
residuals, and a canonical `CardProgram`. For the same inputs and compiler
version, the result must be deterministic.

Fixed stack-counter clauses admit one nonnegative exact, upper, or lower
mana-value bound over an otherwise closed spell target. The source exclusion
and any represented type or color filter remain conjoined with that bound.
The same shape feeds mandatory countering and the existing controller-payment
wrapper. Stack targeting evaluates the effective selected face's mana cost
with announced X through the same mana-value owner used by cast events, rather
than printed mana value or mana actually paid. The full predicate remains a
resolution-time target check. Variable bounds, ability mana-value predicates,
multiple numeric fields, and unrepresented result linkage remain residual.

`compiler/temporary_declaration_templates.py` lowers fixed attack, block,
attack-and-block, or unblockable instructions over the source, one shared
direct permanent target, or one through six targets with a common creature
predicate. An optional target group may choose fewer targets. Qualified targets
preserve fixed current power, subtype, controller, and source-exclusion
predicates through announcement and resolution. The declaration-rule owner
locks each surviving target incarnation until cleanup; later characteristic
changes do not remove its resolved prohibition. A changed predicate before
resolution prevents creation for that target.

Fixed temporary blocker restrictions use a closed descriptor for a blocker
subtype, an artifact-or-fixed-color exception, or a fixed maximum blocker power.
The existing declaration solver reads current blocker characteristics. The
resolved rule stays on the selected creature incarnation through ability loss
and source departure, expires in cleanup, and does not undo an existing block.
The runtime binder independently decodes these descriptors and requires their
fixed-blocker capability.
Chosen qualities, player-relative blockers, two-creature relationships, group
targets and other durations remain outside this production.

A separate source-specific production lowers "target creature can't block this
creature this turn". Its typed journal entry locks both the chosen blocker and
the activation source incarnation. It permits that blocker to block other
attackers and leaves other blockers eligible against the source. Source context
comes from the sealed stack identity; controller changes and ability loss do
not rebind either role, and a returned incarnation does not inherit the rule.

Nontargeted creature-group rules instead retain a typed live public battlefield
query, optionally restricted to the resolving controller or that controller's
opponents. An initially empty group still creates the rule. Later entrants,
current control, and current keywords determine applicability at declaration
time. Keyword predicates consume the shared effective combat keyword query.
This follows the separate game-rule boundary in CR 611.2c rather than the locked
membership of characteristic grants. Both recipient modes use the existing
continuous-effect journal and expire in cleanup. The rule remains distinct from
a granted ability. Ambiguous "other" groups, variable target counts,
independently chosen second-creature relationships, unrepresented predicates, and different durations
remain residual.

Each temporary declaration production recognizes its complete unconditional
instruction. Static restrictions
whose public condition refers to activity this turn retain their ordinary
declaration owner and condition evaluation.

`compiler/extra_turn_templates.py` lowers one through ten fixed extra turns
after the current turn for the resolving controller or one ordinary player or
opponent target. Each turn is a separate existing `extra_turn` instruction;
`rules/extra_turn_shapes.py` requires its exact recipient, source, and target
schema before trust admission. Spell, activated, and normalized-trigger
carriers share the result. The canonical turn-step owner creates queue entries
at resolution, takes the most recently created entry first, and then resumes
normal order. Target departure follows ordinary resolution revalidation;
activation prices remain separate from scheduling. Variable counts, different
insertion points, each-player results, turn-specific riders, and unrepresented
sibling abilities remain residual.

The shared trigger-label normalizer accepts leading ability and flavor names
with numeric words, hyphens, apostrophes, and question or exclamation marks.
Under CR 207.2c-d these names add no rules meaning. The original text and span
remain attached to the node, while the complete triggered body still requires
its existing typed event, condition, result, and capability owners. Inner
dashes, nontrigger bodies, reflexive triggers, and unsupported instructions
retain their existing admission boundaries.

An independently bound source-self trigger can normalize a source-led result
using “it,” “him,” or “her” before exact leaf compilation. This narrow production
admits direct source stat, damage, and counter results. It preserves the full
original source span and delegates incarnation and last known information to
existing owners. Newly created token references, targeted anaphora, union-event
subjects, and unrepresented complete bodies are not rebound to the source.
Effects on the source itself carry the existing logical-object reference;
damage source references retain their ordinary last known source information.

Scheduled each-player and each-opponent upkeep or draw-step triggers preserve the trigger
source's controller separately from the active player. Fixed nontargeted
draw, life, mill, and damage results preserve each clause's original subject.
“That player” and inherited subjects bind to the recorded step player; an
explicit “you” remains bound to the resolving trigger controller. A
current enchanted-object controller schedule uses the reciprocal attachment
and the recipient's controller at discovery. Changing custody later does not
change the queued result's affected player. Unsupported player roles,
dynamic quantities, and sibling abilities remain residuals.
Each-other-player draw schedules exclude the source controller. Controller-only
draw-step triggers use the existing fixed step predicate. Fixed "additional
card" wording lowers to an ordinary draw instruction; the turn's ordinary
draw precedes trigger placement and the extra draw resolves through the stack.
Existing public intervening conditions are checked at occurrence and resolution.
The multi-event compiler also composes an explicit source-entry prefix with
one independently exact distinct same-zone event prefix. Each subscription
retains its own source, controller and characteristic predicates; one shared
result remains one ability. Event alias normalization retains a source-specific
child's typed identity as well as its predicate; a controller-wide or global
child does not acquire that source restriction. A printed once-per-turn limit
requires every subscribed event to remain inside its supported nondeparture
contract. Nested subscriptions, repeated normalized events,
mixed active zones, and unrepresented prefixes remain residual. Qualified
public subjects may similarly subscribe to entry and death or departure using
two independently accepted zone predicates. The canonical predeparture
snapshot supplies departing characteristics and controller.

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
The complete effect-program promotion policy delegates to
`compiler/nonleaf_effect_closure.py`; the lowering facade retains its existing
entry point and recognizer ownership.
For one CardProgram compilation, the adapter passes its locally compiled IR to
the existing program-generation lowering owner. Lowering still validates trust
and constructs fresh semantic programs; it does not parse the same record a
second time. The standalone generated-program API continues to compile its own
IR. No IR result persists across calls or card records.

Whole-hand discard has its own typed compiler and mutation owner. Ordered
composition can lower a fixed draw followed by whole-hand discard even though
the counted-discard and fixed controller-sequence leaf templates reject that
instruction. Integration tests assert the promoted effect order and player
selector; leaf tests retain their narrower grammar boundaries. The whole-hand
impact rule includes those existing consumer expectations.

`compiler/spell_self_exile.py` recognizes a terminal source-self exile clause
only at the end of an ordinary single-face spell's complete source. Its body
must compile independently with unchanged targets and prices. The resulting
typed marker uses the existing replacement-aware resolution destination;
countering and all-target-illegal rejection retain their ordinary graveyard
exit. Partial target resolution and zero target choices still finish the body
and self-exile. Transient spell-copy objects cannot move the original card.
Unknown siblings, another object's name, nonterminal and conditional self
exile remain residual. No runtime Oracle parsing or new movement operation is
introduced.

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

`compiler/champion_nodes.py` lowers a complete Champion keyword line into two
triggered nodes with distinct generated ability identities and one source-line
ability-pair binding through the existing multiple-node keyword dispatcher.
`compiler/champion_templates.py` owns its closed creature
or subtype predicate grammar. The entry choice and explicit leaving return
share the original source incarnation while retaining independent trigger
timing. This form can exile after the source has left; its return does not
install a source-departure duration. Modified subject grammar and independently
unrepresented championed-object event bodies remain residual.

`compiler/conditional_exile_templates.py` separately recognizes the source-
departure form in CR 610.3. Its immutable binding data retain the original
source and the actual exiled incarnations; the return boundary collects one
simultaneous owner-controlled movement through the canonical zone owner.
The compiler emits a paired linked instruction with a versioned conditional
departure timing. Its initial movement requires the original source to remain
on the battlefield; it stores only actual exile outcomes. If that initial
movement itself removes the source, the return occurs immediately afterward.
Created instructions are stored on the original source incarnation and captured
before its zone reset. Single and simultaneous departures use those retained
instructions. A return entry choice resumes the return movement rather than
repeating the source departure; native activation cost and state-based departure
choices preserve their enclosing transaction. Aura attachment subjects are
chosen before simultaneous movement. The conditional capability admits the
closed targeted source-departure form with the ordinary target, replacement,
APNAP, Aura-entry, and retained-completion dependencies. Other specified events,
return zones, unsupported riders, and unrepresented target sets remain residual.
After a choice's movement commits, a conditional return pause retains the
already-computed remaining completion as closed typed data. Resumption validates
the original parent frame and continues those remaining intents and effects;
it does not recompute a consumed sacrifice or discard choice. Preparation uses
the same boundary for retained automatic effects and prepared requests. Casting
cost returns preserve the paid cost and parent spell before priority resumes.
Retired exile incarnations and earlier source lifetimes do not reattach when a
physical card reenters. Multiple original sources retain distinct instructions;
simultaneous specified departures collect one owner-controlled return batch.
Separate entry and leave triggers remain a linked-ability contract under CR 607.

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
`compiler/mixed_target_forms.py` admits two through four flat, unqualified
type/subtype alternatives through this descriptor. It reuses the existing
target-form representation and characteristic matcher. The compiler keeps
card types separate from subtype leaves and shares controller and source
exclusion across the alternatives. Other zones, spell targets, branch-local
qualifiers and independently unsupported sibling instructions retain their
existing boundaries.
The runtime consumes only the resulting typed node and never reparses those
Oracle sentences.

`compiler/temporary_target_interactions.py` composes that same direct-target
descriptor with signed spell-cost X, supported comma-separated keyword lists,
closed typed Protection qualities, a choice of color or two supported keywords,
a closed current-target conditional rider, or a fixed regeneration or
unblockable tail. It keeps the original fixed leaf-parser boundary intact.
X requires an X-bearing spell mana cost on the selected face; undefined X,
variable activated costs and other derived quantities remain residual.
Resolution choices use the existing scalar-choice owner. A recipient-controller
color choice reads the target's current controller when the instruction begins.
Conditional riders evaluate current public characteristics once before creating
the locked layer-6 or layer-7c effect. The same continuous-effect journal stores
temporary Protection as a typed fragment, and existing DEBT consumers enforce it.
Fixed Protection and color choices accept the shared single direct-permanent
target grammar, retaining its characteristic, control and source-exclusion
predicates. Live global and queried Protection grants use dedicated version-two
handlers with complete typed fragments, preserving ordinary keyword-grant
descriptor versions. Their layer-6 recipient set updates with current public
state, and a coupled fixed stat grant uses the identical query.
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
printed order. The controller-discard leaf reuses the affected-player private selection and simultaneous
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
player gain or loss, one opponent-only loss or equal drain, one equal
each-opponent drain, and one each-player loss. Direct targets use the ordinary
public player-target and resolution-revalidation boundary. Each-player loss
uses an exact `all_active_players` scope on the existing loss operation and
commits one simultaneous canonical life batch, including its controller and
excluding players who left before resolution. The existing quantity producer
may supply one shared global count; each player's separate relative quantity
remains unrepresented. Ordered private discard and public sacrifice choices
continue before state-based stabilization, even for participants at zero life.
Unrepresented dynamic amounts, target-player drains that could affect the controller,
life-total setting, exchanges, and unequal drains remain
source-spanned residuals.

`compiler/mill_templates.py` owns one mandatory positive fixed-count Mill
instruction for the controller, one target player, one target opponent, every
active player, or every active opponent.
The same grammar is consumed by spell, triggered, and activated contexts and
feeds one immutable current-library-top plan into the canonical simultaneous
zone-transition owner. All-player instructions snapshot every affected
library before one simultaneous batch, with APNAP ordering retained for
replacement choices and normalized trigger placement. A short library mills as
many cards as possible; an empty library alone causes no loss from milling.
destination replacements, actual public results, target revalidation, APNAP
zone triggers, rollback, privacy, and replay remain shared. Optional and cost
Mill, unrepresented dynamic or half-library quantities, linked
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
replay authority. Independently quantified card clauses retain one slot per
printed kind before single-union parsing. Type intersections and exclusions,
qualified creature subtypes, and known literal subtypes remain distinct;
arbitrary qualifier phrases cannot become subtype names. Unrepresented layout
predicates reject the complete clause, including an otherwise represented
alternative. Variable counts, another player's library, numeric or named
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

External entry-state lowering preserves the unconditional type-union descriptor
and uses a separate qualified version for nonbasic or subtype exclusions,
mixed fixed type subjects, and a source tapped or untapped condition. It composes
the existing public type-query parser into a bounded list of flat leaves; the
runtime receives no Oracle text. One controller relation applies to every leaf.
The entry owner makes scopes disjoint and binds them to one replacement
occurrence, so an object matching several subjects still has one ordering option.
Source-name conditions use the ordinary compile-time self-reference model.
Archaic played-by subjects, intrinsic turn-number conditions, and unrepresented
subject predicates retain material residuals.

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
Card-only graveyard conditions retain their versioned token exclusion at this
consumer boundary. A plain public graveyard object count is admitted without
enabling other quantity reductions or arithmetic. Historical version-one
queries retain their earlier decoding and admission behavior.
The mandatory delayed-draw leaf retains its separate temporal scope and is not
lifted into optional or closed multi-result programs by this component gate.

`compiler/fixed_homogeneous_target_sets.py` lifts an existing reviewed scalar
destroy, exile, tap, untap, battlefield-return, own-graveyard-return, or public-
graveyard-exile clause into one interchangeable public target set. The grammar
accepts exact counts from two through six, one-or-two, and up-to one through
six. Battlefield returns reuse every closed direct-permanent predicate;
own-graveyard returns reuse fixed card types, type intersections and unions,
subtypes, supertypes, colors, color cardinality, and fixed mana-value upper
bounds from zero through twenty. The numeric clause preserves the complete
card predicate and passes through shared offer and resolution target checks.
Qualified permanent-card
predicates retain that explicit card domain when the battlefield parser's
permanent zone would otherwise make it implicit. The owner adds only one
shared count and resolution-revalidation capability, and supports a single-
graveyard constraint through target ownership. The same scalar leaves also feed
the existing optional and closed-sequence compilers. Heterogeneous roles,
divided or dynamic quantities, linked results, compound or conditional tails,
other players' graveyards, non-owner-hand destinations, and unsupported
characteristic dependencies remain source-spanned residuals.
`compiler/any_number_target_sets.py` reuses these complete homogeneous results
and the fixed temporary characteristic-set owner for “any number of targets”.
Its version-two count parameter permits zero through the authoritative finite
object universe without requiring mana X or imposing a fixed six-target limit.
The result's typed maximum reads the original chosen target roles, including
targets that later become illegal. Unknown result siblings, divided quantities,
and aggregate target constraints remain residual.

`compiler/target_count_cost_nodes.py` lowers a complete fixed ordinary-mana
sentence that charges for each target beyond the first. Strive is an ability
word; the sentence defines its mandatory price. The existing cast-cost component
registry owns this source-pinned descriptor. The price is multiplied by the
original chosen target count minus one, floored at zero, before ordinary total
cost adjustments. This source family requires one homogeneous unrestricted
target group; heterogeneous or independently quantified price groups remain
residual. It creates no Kicker or mana-X casting decision.

Legacy creature-or-Vehicle and historic-permanent target alternatives retain
their existing typed union authority. Only schemas closed by the shared direct
permanent predicate decoder request that decoder's characteristic dependencies;
legacy union fields do not enter its single-predicate vocabulary.

Public quantity conditions reject keyword-qualified existence queries before
constructing their closed quantity descriptor. An unsupported condition keeps
its source-spanned residual; a successful direct-target predicate does not
imply that the narrower quantity owner can evaluate it.

`compiler/conditional_recipients.py` composes fixed self and controlled-set
clauses beneath one complete public condition. Closed recipient roles preserve
source-only, other-creature and other-land-creature scope, so a source that also
joins the set receives one modifier. “Exactly one creature” uses the existing
minimum and maximum count leaves; “that creature” becomes the current controlled
creature set under that exact condition. Lieutenant checks current battlefield
presence of a commander owned and controlled by the source controller, using
the designation rather than printed identity. Each descriptor independently
requires the recipient capability and any commander designation authority.

`compiler/public_zone_conditions.py` binds fixed public zone facts to the same
condition descriptor: an opponent has an empty hand, an opponent owns a card in
exile, or one library has at most a fixed number of cards. Opponent relations
follow the current source controller. Library thresholds quantify each library
independently, including the controller's; they never sum library sizes. The
compiler and runtime independently require the public-zone condition capability.
Private identity predicates, aggregate-library wording and unsupported companion
abilities remain residual.

`compiler/declaration_nodes.py` composes one independently exact
recipient-relative declaration requirement or restriction with fixed
power/toughness and represented keywords behind a closed public-state
condition. The versioned conditional declaration descriptor retains the same
source condition and target for layer 6 and layer 7c results. Current
conditions remove or restore the declaration fragment through ordinary ability
evaluation; declaration offers and accepted commands use the existing solver.
Open suffixes, variable modifiers, additional costs and nonrecipient rule
scopes remain residual. Earlier conditional descriptor schemas retain their
existing field and fragment contracts.

`compiler/public_state_queries.py`, consumed by
`compiler/continuous_templates.py`, lowers fixed controlled permanent sets that
gain supported keywords, or gain fixed power/toughness and supported keywords,
until end of turn. The legacy label-based set codec accepts only ordinary
keyword labels;
Protection qualities and triggered fragments require a separately represented
temporary-set descriptor and are never discarded during this lowering. Static
live grants and direct temporary Protection retain their typed fragments.
Flat unqualified creature-subtype alternatives retain their existing temporary
set behavior when represented by the shared receiver-union parser. Mixed or
branch-qualified unions remain outside this codec. An independent controlled
set uses its own selection scope even when another instruction targets a player;
queries that consume that player still validate the player's target schema.
`compiler/fixed_public_characteristic_sets.py` extends the same
resolution owner to all creatures, creatures opponents or one target player
control, current attacking or blocking creatures, and source-excluding attacking
creatures. Both grammars feed one closed query validator and accept the
complete combined keyword and signed power/toughness result in either
printed order. The existing declared-amount composition also accepts that
result with a typed public count or scalar quantity; promotion independently
validates the projected fixed result and every required quantity owner.
Resolution evaluates only cycle-safe current type, subtype, color, supertype, token, controller, and
public combat-state facts, then locks the matching logical objects. Bare
subtype resolution sets include noncreature Kindred members. A fixed power or
toughness bound on a creature recipient reads exact current effective stats
at resolution before membership is locked; unknown values do not match.
The runtime
sends ability additions through shared layer 6 and power/toughness changes
through layer 7c over that same locked set. The source leaving or changing
controller does not end the effect, later entrants do not join it, returned
objects are new logical objects, and cleanup expires both layers. Multiple or
opponent-only targets, attachment-relative, chosen, counter-qualified, tapped,
modified, keyword-qualified, dynamic, conditional, quoted, Protection,
type-changing, and variable-duration forms remain source-spanned residuals.

Shared live battlefield queries preserve the distinction between a bare
creature subtype and an explicit creature noun. Bare subtype sets, including
subtype-qualified tokens, include noncreature Kindred permanents of that
subtype. Explicit creature sets retain their creature type constraint.
Static keyword and fixed modifier consumers use that same predicate; a
modifier does not define power or toughness for a noncreature permanent.

`compiler/fixed_characteristic_settings.py` also lowers constant settings for
the source object behind a closed public scalar or graveyard-count condition.
The version-two setting descriptor retains the condition and source logical
incarnation; existing version-one public and attached descriptors retain their
decoder and handler identity. Type, color, ability, base-stat and fixed modifier
operations reuse the ordinary layer owner. Conditions remain live, and base
stats do not erase counters or later modifiers. Battlefield characteristic-
dependent conditions, dynamic settings and quoted siblings remain residual
until their dependency and composition contracts are represented.

Explicit “in your graveyard” static keyword grants have a separate active-zone
production and capability. They use the source card's owner for “you,” evaluate
the current public condition, and grant to the current battlefield query.
Graveyard arrival retains its zone timestamp; enabling the condition later does
not retimestamp the grant. Moving the source out of the graveyard ends it.
Nonbasic lands with the named basic land subtype satisfy land conditions.
The legacy keyword handler retains its schema. Separate typed-grant handlers
admit one independently closed quoted ability, with an optional represented
public-state condition. Their recipients stay on the battlefield while the
giver stays in its explicit graveyard zone; the recipient supplies activation
source and controller. Mixed fixed keywords remain separate modifier results.
Hidden conditions, other source zones, fixed stat changes and unrepresented
siblings remain outside this graveyard grant contract.

The `source_renowned` public fact reads the canonical renowned designation.
Its fixed boolean threshold and absent parameter use the existing version-four
public-fact codec and independently require the designation capability. A
counter is not a proxy for this state: removing all counters does not end it,
while a new battlefield incarnation starts without it. Ordinary ability
removal still governs the conditional ability that consumes the fact.

Qualified “one or more” player-damage triggers compile native source type,
subtype, color and supertype predicates alongside controller, source-zone and
combat restrictions. Bare subtype subjects retain their permanent domain.
One simultaneous damage batch coalesces once per observer and damaged player;
closed draw and source-counter bodies retain their existing result owners.
Unsupported numeric, token, face-down and counter predicates remain residual
until the normalized source snapshot represents those distinctions.

`compiler/source_counter_anthems.py` extends the existing live static anthem
owner with fixed signed power/toughness coefficients per named counter or per
individual counter of any kind on the source. The recipient query stays live;
the shared continuous source context supplies the current counter count for
layer 7c. Source departure or ability removal ends applicability, and later
matching creatures receive the current bonus. Version-three anthem descriptors
add only `source_counter`; version four explicitly selects the source total.
Fixed version-two descriptors retain their decoder. Ambiguous recipient
pronouns, counters on another object,
and unrelated quantity expressions remain residual in this production.
Chosen-color or chosen-type wrappers validate their nested descriptor against
the existing chosen-characteristic owner before exact lowering. A source-counter
anthem inside that unrepresented wrapper remains residual.

Public creature sets and direct targets also represent “with a counter on it”
through the version-three permanent-state predicate. Its minimum counts
individual counters of every kind, while named-counter predicates retain their
old shapes. Current static grants and Defender exceptions recheck the predicate;
target results revalidate it, and resolution characteristic sets lock recipients.
Public counter-total quantities use the existing layer-five collection reducer,
so a controlled-creature total counts counters rather than qualifying creatures.
Both counter query authorities are explicit compiler/runtime dependencies.
Unsealed event-time counter links and unrelated result clauses remain residual.

Modular entry descriptors declare the same quantity-replacement capability
required by their registered self-entry counter component. Missing replacement
authority therefore prevents exact keyword admission before runtime binding.

`compiler/fixed_target_effect_sequences.py` also owns one fixed source-
characteristic result shared by activated and normalized triggered contexts.
It accepts a fixed power/toughness modifier, including an explicit +0/+0 that
leaves the source unchanged, plus supported keywords, one or
two supported keywords, a colorless or all-colors source change, or a
fixed artifact animation with literal colors, creature subtypes, base
power/toughness, and supported keywords. The runtime commits every represented
layer-4, layer-5, layer-6, layer-7b, and layer-7c component atomically with one
timestamp and one locked source incarnation. The same canonical keyword map
extends targeted spell and activated effects without a family-local ability
check. Layer-4 animation feeds the existing cycle-safe dynamic-characteristic
boundary before layer-7a counts, and the state-based owner detaches Equipment
that becomes a creature. Dynamic or
chosen values, copies, text or control changes, declaration riders,
Protection, unrepresented landwalk predicates, unsupported keywords, and non-until-end-of-turn
durations remain source-spanned residuals.
The shared map retains the five basic landwalk variants and adds Desertwalk,
artifact, Legendary, nonbasic, Snow, and Snow/basic-land-type predicates.
Ordinary grants retain the basic-land-type consumer; qualified grants declare
the qualified Landwalk consumer. Blocking reads each defending phased-in
land's current effective types, subtypes and supertypes. One land must satisfy
every part of a compound predicate. Nonbasic means the absence of the Basic
supertype, independently of basic land subtypes. Other evasion restrictions
still apply.

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
`compiler/temporary_keyword_removal_templates.py` adds an explicit
schema-11 variant to the same temporary-characteristic operation. It combines
represented fixed keyword removals, optional fixed grants and integral P/T
deltas without setting base P/T. Source, one creature target and closed public
creature-query selections retain their existing predicate owners. The rules
decoder validates the complete descriptor before the existing continuous-effect
journal commits layer-6 and optional layer-7c components. Query membership and
incarnations are locked at resolution; later entries do not join, source
departure does not undo a resolved effect, and normal cleanup ends it.
Removal permits later grants under ordinary timestamp order. A separate fine
capability and runtime-derived requirements guard this variant. Choices,
prohibitions, other durations, triggered-fragment removals and independently
unsupported siblings remain residual. Earlier schemas keep their payloads.

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
forms remain one descriptor so canonical layer ordering is preserved. Fixed P/T may combine directly with supported keyword removal without an ability grant. Removals retain layer-6 timestamp order; a later keyword grant may restore the ability, and reattachment or source departure updates the live relation. The same
descriptor may append one or two already-exact declaration requirement or
restriction fragments, including beside a fixed attached modifier and keyword.

Standalone keyword grants also accept enchanted artifacts, battles,
enchantments, lands, planeswalkers, and unqualified permanents. These subject
forms retain the existing live-relation descriptor and typed Ward or other
represented keyword fragments. The extension accepts the keyword clause only;
existing type, base-setting, and additive productions keep their owners and
serialized shapes. A noncreature may receive Shroud, Indestructible, or Ward
without an invented creature prerequisite.

`rules/attached_keyword_requirements.py` derives independent runtime minima
from the validated descriptor, the canonical keyword catalog, and typed
fragment ownership. Declaring the general attached-modifier capability cannot
substitute for a granted keyword's owner. Subject schemas reject unknown or
duplicate types, and unsupported keyword text or sibling clauses remain
residuals.

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
uses the recipient as the granted ability's source and locks a triggered
ability's controller when it triggers. Removing the attachment or grant later
does not remove an ability already on the stack.
An Oracle list comma immediately inside the closing quote can separate the
quoted child from a following outer conjunction. The compiler preserves the
original source span and validates both the complete outer attachment shell
and independent inner body; inner punctuation and unsupported tails remain
material. A supported ability grant can also compose with an additive type
or subtype without requiring a P/T modifier. The fragment
preserves only activation costs already committed by the canonical activation
transaction, including fixed mana, tap or untap, source sacrifice, life
payment, selected public objects, and the represented restricted-mana profile.
Version-four granted activation fragments retain any independently represented
fixed-output spending predicate that has no additional result. Legacy fragment
versions keep their exact admission and payloads. The outer grant and hidden
child independently require the restricted-grant capability. The receiving
object supplies activation identity, controller and costs; produced units keep
their canonical spending restriction after the giver leaves. The finite
restriction grammar treats “instant and sorcery spells” as the same two spell
types as “an instant or sorcery spell”. Additional results, unknown restrictions
and unsupported output quantities remain residual.

Live quoted grants also admit pinned land subtype recipients and flat
land-or-creature-subtype unions through the existing public query owner. The
controller relation applies to every branch, matching an object once. “Other”
excludes the source; Caves remain land recipients rather than an invented
creature type.

The composite production admits two through four independently closed quotes
for an attachment or public query. It also admits represented all-ability
removal with one or more replacement quotes, and exact land-subtype removal
without changing card types or supertypes. Children use distinct quoted-slot
identities while ordinary single-quote identities retain their existing form.
Every child closes before the parent is emitted. The shared characteristic
emitter removes abilities before adding replacements within one layer-six
effect. Unrepresented settings, conditions and recipient queries invalidated
by an unowned type change remain residual.

Other quoted static or declaration prose, named or external source
references, hidden-zone recipients, and independently inexact bodies remain
material residuals. Granted fixed source-counter removal costs also remain
residual until the granted-ability fragment schema can carry that typed cost.

Basic-type addition also has an explicit version-two scope for the current
controller's nontoken creatures. It adds the land card type and the specified
basic subtype in layer four, retaining other types. The existing intrinsic
mana owner supplies the subtype's ability before layer-six removal or grants.
Version-one land-only scopes retain their historical payloads and operations.

`compiler/granted_static_costs.py` also composes one fixed source-target opponent
spell tax with a represented permanent attachment, public-query or public-state
grant shell. The independently compiled child is hidden from printed discovery
and carries the dedicated grant capability. `StaticComponentSpec` version two
binds its trusted semantic key and closed cost event; version one retains its
historical payload. Current fragments preserve identical physical grant
occurrences. The receiving object and its controller supply the child's source
and opponent relation, while the outer continuous owner supplies current
membership and lifetime. Ordinary role-static child identities keep their
existing generation path. Other static quoted bodies and static temporary or
token extras remain residuals.

`compiler/granted_keyword_event_nodes.py` composes one unquoted fixed event
keyword with the same attachment, live-query, or public-state grant shells.
The existing `keyword_event_effect_nodes.py` owner supplies the complete inner
event, condition, targets, effects, parameter bounds, and capability closure.
The compiler emits the ordinary granted-only program and
`GrantedTriggeredAbilitySpec`, with the original grant's source span. Discovery
uses the receiver's current grant fragment; printed programs retain their
printed static-component requirement. Independent providers grant independent
instances, and a pending ability retains its receiver source and trigger
controller after the grant ends. Multiple fixed event keywords in one grant
line and independently unsupported shells or parameters remain residual.

`compiler/temporary_quoted_ability_nodes.py` lowers one explicit quoted
activated or triggered ability on a direct creature target or closed public battlefield set until
end of turn. The independently compiled child uses the recipient as its source
and its controller as the activating player or trigger controller. Death
triggers use the existing predeparture fragment snapshot. Set recipients are selected at
resolution and retain their original incarnations. The existing version-seven
single-recipient bundle remains unchanged; version eight carries the fixed
public query. Version nine couples fixed signed power/toughness changes and
one quoted child on a direct creature through one multi-layer timestamp.
Static quotes, multiple quotes, longer durations,
external source references and inexact children remain residual.

Intrinsic entry-copy syntax lowers immutable selection and exception values
through `compiler/entry_copy_templates.py` and the registered
`semantic_runtime/entry_copy.py` replacement owner. Its nontarget choice
captures current public copiable values, applies the existing typed copy
exceptions and runs at the enters-copy replacement priority. Copied intrinsic
entry producers activate only after that choice. Acquired intrinsic copy
abilities participate before entry: immutable form conditions activate only
the current form, and candidate alternatives share one ability occurrence.
Accepting or declining that occurrence consumes its opportunity for the same
entry event, including when a later copy reacquires it. The canonical entry owner
retains physical Oracle identity while copied program provenance selects the
copied static, activated and triggered descriptors. Source departure preserves
the snapshot; zone departure clears the copy. A copied Aura uses the existing
attachment choice before entry, with a retained replacement result through
save/load and replay. Unknown quoted exceptions, hidden-zone references,
unrepresented copy selectors and unsupported copied programs remain
outside the represented composition.

`compiler/conditional_granted_ability_templates.py` wraps one independently
exact quoted child with the existing closed public-state characteristic
grammar. It retains fixed coupled keywords and power/toughness modifiers in
the same condition. A distinct schema-v2 runtime handler emits the typed
fragment through the shared layer-6 owner; the earlier characteristic-only
schema remains strict. An unsupported condition, quoted child, nested static
grant, multiple quotation, or material sibling prevents complete-card closure.

`compiler/conditional_color_grants.py` extends that shell with one fixed
self-color replacement and the same independently compiled child. Its fine
capability retains coupled ordinary keywords and signed additive modifiers.
The registered component begins in layer 5 and shares the existing source
incarnation, condition snapshot, layer-6 grant, and layer-7c modifier owners.
Characteristic-dependent conditions that can feed back into the color change,
additional or variable colors, wider recipients, and unsupported children stay
residual. Earlier characteristic-only descriptors retain their strict shape.

The same compiler also combines a closed public-state predicate with an
independently compiled keyword grant. Fixed generic ward, protection, and
represented combat or spell-cast trigger keywords retain their complete typed
fragments and consumer capabilities. The versioned conditional keyword
descriptor shares the existing layer-6/7c lowering. Unknown keyword parameters,
toxic query grants, hidden conditions, and unsupported siblings remain residual.

Closed public-state predicates may also gate an independently compiled spell-
cost modifier. The conditional cost descriptor retains the existing spell
predicate, controller scope, total-cost adjustment and current-ability coverage.
Named sources remain data in the compiled condition. Hidden, nested or unknown
conditions and unsupported cost bodies remain residual.

Explicit conditional self-graveyard casting permission also reuses the public-
state grammar. The compiler retains a typed condition, graveyard activity and
ordinary cast semantics; Max Speed and monarch predicates retain their own
capability requirements. Additional costs, alternative zones, hidden conditions,
permission riders and unsupported siblings remain outside this production.

`compiler/paid_graveyard_templates.py` and its record-aware node owner lower a
complete ordinary intrinsic graveyard price. A fixed mana replacement remains
distinct from additional life, hand discard, creature or artifact/creature
sacrifice, and another own-graveyard creature exile. A decorative title is
retained in the source span while the complete clause supplies its semantics.
The node declares the payment's actual owners and current-ability fragment;
independent runtime decoding rejects a missing owner or changed price. The
record-aware production excludes variable printed mana, other casting methods,
printed additional-cost companions and unrepresented entry consequences.

Attached characteristic grammar represents fixed modifiers per Equipment,
Aura, or Aura/Equipment attached to the recipient. The compiler emits the
versioned recipient-relative public quantity; the ordinary attached owner
keeps any paired keyword fragments and applies the resolved count in layer 7c.
Unrepresented relative qualities and hidden attachment quantities stay residual.

The existing fixed public setting owner also accepts static removal of
represented fixed keyword strings. The compiler retains the complete live
public query and each keyword capability; the unchanged layer-6 emitter uses
ordinary source timestamps. Later entrants join the live query, later grants
may restore an ability, and source departure removes its contribution.
Prohibitions, unknown or triggered-fragment removals, chosen values and
unrepresented conditions remain residual.

Fixed live-set stat and keyword composition accepts singular “each ... gets
... and has ...” wording alongside the plural form. Both use the same public
query, subtype union, source exclusion and layer owners. Unknown subjects,
variable results and unsupported siblings retain their existing residuals.

Fixed characteristic subjects also represent flat unions of public
battlefield type, subtype, snow and color sets. Repeated “you control” clauses
share one controller relation. A leading “other” excludes the source from the
whole union, while “Skeletons you control and other Zombies you control”
excludes it only from the Zombie branch. The existing query matcher applies a
union once per object; it does not emit an independent bonus for each matching
branch. The compiler declares the receiver-union capability in addition to the
existing stat or keyword consumers. Recursive, hidden, numeric and
ability-qualified unions remain residual.

The public quantity compiler recognizes single-color and two-distinct-color
devotion through a dedicated versioned reduction. It retains the CR 700.5a
timing boundary instead of treating devotion as an ordinary color or object
count. Fixed static increases lower to typed current components; existing
quantity consumers declare the devotion producer dependency at admission.
Unknown colors, unsupported increases and unrepresented siblings remain residual.

Counted token wording can use a complete public value after “equal to,” while
the ordinary fixed-token parser still owns the entire definition and abilities.
The quantity resolves once before the shared token replacement transaction.
Mandatory controller-scoped token doubling lowers to the already registered
quantity replacement descriptor with current ability coverage. Unknown values,
definitions, linked results and wider doubling scopes remain residual.

Public quantities also recognize “opponents you have” through an explicit
versioned roster scope. It has no object predicate, hidden-zone fields or
counter relation. Static and effect consumers declare the public player-count
producer separately; team formats and unrepresented opponent predicates retain
their existing support boundaries.

Public battlefield subjects also accept a single named negative color, such as
nonwhite creatures. They preserve current controller and source-exclusion
qualifiers in schema-nine queries, with an explicit negative-color capability.
Static anthems and grants use current color after layer five; fixed end-of-turn
characteristic sets lock recipients at resolution. Negative keywords, hidden
queries, ambiguous compound colors and unrepresented siblings remain residual.

Boolean public conditions retain complete atomic parsing before splitting
an `and` or `or` composition. Each leaf must independently compile, and the
versioned condition keeps its operator and producer dependencies. Ambiguous
attachment pronouns, mixed operators and numeric characteristic predicates
without a cycle-safe timing owner remain residual. Existing unsupported
intervening-if source-counter and attached conditions remain excluded inside
a composition as well.

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
unsupported previous-turn facts and open dynamic counts remain material
residuals. See [ADR 0099](../adr/0099-typed-public-declaration-conditions.md).

Attached all-able-blocker clauses retain their rule on the originating Aura or
Equipment through a version-two declaration requirement fragment. They do not
grant that rule as an ability to the recipient. Fixed attached-characteristic
and controller-graveyard conditions use the existing public-state condition
owner; a mixed fixed characteristic clause keeps its characteristic modifier
separate from the source-owned requirement. Exact target and source-self
instructions lasting through this turn lower through the existing declaration
effect operation to a version-three journal entry. Source-self instructions
bind the sealed battlefield incarnation. Filtered blockers, ambiguous
antecedents, other durations and live recipient groups remain residual in this
production.

Fixed additional and unlimited blocking permissions lower to typed layer-6
capacity fragments for source, attachment and closed public-query recipients.
Fixed public-state conditions use the existing snapshot owner. Printed
Vigilance and fixed P/T grants in the same sentence remain independent results.
The shared declaration solver selects distinct attacker sets up to current
capacity while retaining costs and maximum requirement satisfaction. Open
quantities and independently unsupported siblings remain residual.

Ordered effect programs may include one independently closed all-damage
prevention shield. Its fixed mode, turn-bound duration and complete typed scope
must close before the program is trusted. An established direct target remains
index zero through later modifier, untap and prevention instructions; shield
creation cannot add a new target. Unknown scope fields, derived amounts,
additional aftermath and unrepresented target references remain excluded from
this component contract.

The shared direct-permanent predicate accepts the pinned artifact, enchantment,
and land subtype vocabulary in bare targets. "Target Equipment," "target
Aura," and "target Mountain" require that subtype on a current permanent;
they do not add a creature-type requirement. Existing controller, source
exclusion, target selection and resolution-incarnation checks remain shared.
Unknown subtypes and unsupported qualifiers still fail closed, and this
vocabulary does not imply that every effect or quoted ability using it closes.

Fixed scheduled triggers distinguish the controller's first/precombat main
phase from second/postcombat main phase with both phase and step predicates.
They consume the existing normalized boundary event and ordinary APNAP trigger
placement. Main-phase mana results remain stack-using triggered abilities.
Current source ability presence and represented intervening conditions retain
their existing checks. Additional main-phase ordinals and unrepresented
extra-phase or skipped-phase instructions remain outside this slice.

Intrinsic reveal-or-tapped entry clauses lower through
`compiler/entry_reveal_templates.py` to a closed hand subtype query. The
prospective controller sees only matching private hand cards and may decline.
The canonical replacement owner completes that choice before entry; only the
chosen card becomes public knowledge. Revealing does not force untapped state,
so independently applied tapped-entry replacements still apply. Copied entry
abilities use the same query with the copier's prospective controller. One
unchanged hand card can satisfy distinct simultaneous reveal choices. Unknown
qualifiers, paid reveals, and reveal-or-current-battlefield condition compounds
remain residual.

Printed Enlist compiles separate typed attack-cost and reflexive growth
components. The shared attack declaration exposes optional donor assignments
and validates untapped state, control history, haste, source exclusion and
distinct use. Reflexive effects retain source and donor incarnations and reuse
the existing live-or-last-known scalar power owner. Multiple printed instances
remain independent, and unrepresented Enlist-dependent sibling instructions
retain their residuals.

`compiler/exert_nodes.py` lowers one complete "You may exert this creature as it
attacks. When you do" paragraph into a typed optional-cost fragment and a
separate linked result program. The independently closed result body keeps
its ordinary targets and effects. Self results bind the original source
incarnation. `combat.attack.exert` depends on the player-locked
`untap.step.exert_player` duration owner and ordinary trigger placement;
unknown bodies or missing dependencies keep the paragraph residual. Separate
paragraphs retain distinct linked identities, and paying one cost cannot
publish another paragraph's result. Conditional attack permissions and other
Exert producers require their own typed contracts.

Fixed all-damage prevention shares the existing scope codec for static and
turn-bound forms. Closed source and recipient predicates include attacking
creatures, own creature tokens, enchanted creature sources, accepted combat
pairings, red or snow recipients, fixed absence of +1/+1 counters and matching
colors on distinct creatures. Sealed damage facts and ordinary redirection
keep these predicates exact across replacement choices. Unknown quantities,
chosen-color links and independently unsupported siblings remain residual.

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

Self permissions conditioned on current power or toughness use a separate
`combat.attack.permission` event and a closed declaration component. They read
complete current characteristics when declaring attackers, including a
qualifying creature controlled by the source's current controller. The generic
layer-six numeric feedback guard remains in place. An ambiguous conditional
pronoun, unrepresented condition, or missing capability keeps the ability
residual; this numeric owner does not admit counter or attachment conditions.

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

`compiler/toughness_assignment_templates.py` lowers the closed rule that
creatures assign combat damage using toughness. Its source-local fragment
records intrinsic, attached, global or controller scope, current Defender or
Vigilance qualifiers, fixed toughness-greater-than-power comparison and
controller-turn applicability. Coupled attached stat bonuses and Defender
permissions retain their existing characteristic owners. Temporary direct
creature targets create duration-bound rule effects through the canonical
journal; the represented Haste/Defender/toughness instruction composes three
independently closed results over one shared target. Unsupported comparisons,
result siblings and cost semantics remain residual.

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

Predefined Role definitions compile into separate granted-only static and
triggered child programs. Enchant and child references are explicit token
fragments pinned to the creating card program. These child programs cannot
become abilities of the creator. Closed source and direct-creature attachment clauses enter through the
replacement-aware Role choice owner. Every Role definition retains its full
child capability closure. Activated clauses remove only compiler-recognized
reminder text before the existing typed cost and Role effect gates. Unknown
or linked creation grammar remains residual.

Manifest instructions are represented as a separate controller-private choice
family. Normal top-card entry and Manifest dread retain distinct modes. Dread
selects one private looked-at card, manifests it, and then moves the remainder
to the graveyard. Manifested normal-layout creatures with current whole-card
closure may turn face up for an ordinary printed mana cost; represented Morph,
Megamorph and Disguise alternatives retain separate offers. Complex mana and
unsupported card-form or face-up behavior remain explicitly unavailable. See
[ADR 0115](../adr/0115-typed-manifest-entry-and-turn-up.md).

Source power and toughness intervening conditions use one fixed numeric
battlefield creature predicate. Discovery and resolution both read complete
effective characteristics; unknown statistics remain unavailable. Static
conditions using these numeric source facts remain residual until a separate
layer timing contract is represented.

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
path. Complete fixed modifier bodies may say “an additional” or “also”; each
bonus adds independently to the other applicable layer-7c modifiers and retains
its live condition. These words do not introduce a remembered value, a variable
bonus, or a repeated effect. Attachment-relative “it” binds to the explicit
enchanted or equipped object; other source pronouns remain source-bound. Public state may participate
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
Schema-five conditions represent one fixed power or toughness comparison for
a creature currently controlled by the condition player. Event and resolution
consumers require the explicit numeric-condition capability and read complete
effective stats. Static characteristic productions, aggregate total power,
variable thresholds and linked numeric facts remain residual.
Schema-seven public facts compare one printed color against all five current
permanent color counts. The layer-5 public query includes each color of a
multicolored permanent once, excludes phased-out objects, and reads every
battlefield seat. A tie includes all-zero counts; colorless is not a sixth
color. The condition follows current color and zone changes before state-based
stabilization and does not inspect hidden card identities.

Complete enchanted-creature additive stat clauses may also provide a fixed
otherwise branch. The compiler combines an unconditional otherwise modifier
with the difference between the two fixed branches under the live recipient
condition. Both components retain the same source and attachment lifetime.
Otherwise branches that set base statistics or change permissions remain
residual.

Other most-common or tied domains, dynamic amounts, top-library, chosen,
hidden-identity, city blessing, dungeon, initiative, speed, sticker, crime,
dice, open arithmetic, unrepresented otherwise branches, per-opponent aggregate, unowned
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
identity. The multi-sentence component owner groups an existing leaf's complete
selection or other instruction. The bounded grouping passes every flattened
effect through the same capability resolver before composition. Private library
choices finish before later siblings; preceding damage does not repeat after
save/load. Unsupported cross-component references stay residual. Independently
exact optional atomic components may use the fixed optional-effect owner
without changing sibling scope. Conditional, random,
repeated, variable, linked-result, unsupported optional,
shared-subject, multi-target-group, quoted-boundary, parenthetical-boundary,
and more-than-four-component programs remain source-spanned residuals.

`compiler/bound_effect_programs.py` owns the complementary single-reference
composition boundary. Two through four mandatory components may share one
player subject or reuse one explicitly established player or battlefield target
through “it,” “that player,” “that creature,” or “that permanent.” It retains
the original target qualifiers and source exclusion, verifies reference-domain
compatibility, and emits the existing effects in printed order. Pronoun
normalization uses a generic permanent reference before restoring the
original target schema, so a creature-bound prevention instruction retains its
closed prevention scope instead of switching to an incompatible leaf shape.
If that generic leaf rejects the instruction and the original target requires a
creature, a creature form may use its already closed leaf before the original
target schema is restored. A noncreature target cannot use that retry.
Separate explicit target occurrences never become one target merely because their
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
identity. Finite `You may have` clauses with an explicit source or target subject
and deal, gain, get, or lose normalize to that same independently closed atomic
body. Targets are chosen before the resolution-time apply-or-decline choice;
target reentry cannot substitute a new incarnation. Optional costs, permissions,
replacements, pay wording, unbound event pronouns,
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
nothing. Normalized trigger bodies retain their bound result references inside
the payment wrapper: source characteristic changes use the triggering source's
logical incarnation, and entering-object token copies use the sealed event
object and its current or last-known copiable information. A returned source
incarnation receives no characteristic change from the old trigger. A paid
counter result retains the mana-payment wrapper; the counter-trigger compiler
does not interpret `you may pay` as a free optional counter-placement choice.
Placement targets are chosen before the resolving payment, and decline places
no counters. Variable,
hybrid, Phyrexian, snow, zero, restricted, and nonmana
costs, reflexive `when you do` forms, nested or repeated choices, linked
results, and multi-effect or independently inexact bodies remain residual.

Fixed optional payment shapes also recognize the target origin retained by a
typed token-copy recipe. String target placeholders and recipe origins share
the same independently closed target/copy authority; an unsupported recipe
still fails its canonical shape validator.

The versioned fixed payment descriptor also represents a positive printed
energy cost before independently closed effects. Event-aware composition
retains the source incarnation inside its paid result and preserves targets
chosen at trigger placement. Variable or repeated energy, combined resource
prices, reflexive triggers, and unrepresented result leaves remain residual.

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
The version-five cost leaf composes one positive ordinary mana vector with one
owned hand-card discard in that same optional decision. The advertised choice
requires both resources. Completion validates both before canonical mana and
discard intents, and retains already-paid mana through a pending discard
replacement. Its independent capability is also derived from the payload.
Other alternative/compound/dynamic costs, random/named payments, paid-object or linked
results, reflexive triggers, nested/repeated choices, unsupported leaf owners
and wider grammar remain residual.

Fixed kicked-spell additional results use a versioned instruction in the
existing public-condition owner. The recorded stack Kicker fact selects only
the fixed nontargeted result after its independently closed mandatory prefix.
A copy preserves that fact; missing history rejects instead of becoming unpaid.
Invalidated mandatory targets still prevent the whole spell from resolving.
Counter-source attribution remains data owned by the existing counter
transaction. Additional conditional targets remain residual.

`compiler/kicked_self_replacement_templates.py` composes the sealed Kicker
fact with the same owner's self-replacement contract using schema version 4.
An ordinary single-face instant or sorcery selects exactly one complete fixed
untargeted branch. Both branches retain independent child capability closure,
and self-replacement precedes external draw or token replacements. A fixed
"those tokens" count preserves the original token's complete recipe through
the existing token-count vocabulary. Copied spells inherit the paid choice
under the copy's controller without paying again. Target changes, dynamic
quantities, permanent triggers and unsupported siblings remain residual.
`compiler/replacement_residual_nodes.py` owns the marker boundary after closed
static dispatch, retaining the original source span, ordering blockers and
unowned replacement node when neither reviewed self-replacement closes.
Schema 4 also adopts fixed untargeted damage sets. The existing damage owner
retains source attribution, current or last-known source characteristics and
simultaneous current recipient queries; the selected amount stays a literal.
The compiler binds an alternative's "it deals" only to the already represented
damage-set source. Other source-relative values and earlier condition schemas
retain their existing boundaries.

`compiler/kicked_same_target_replacements.py` adopts schema version 5 for one
unchanged public target role. Only an explicit same-creature anaphor or fixed
damage-amount shorthand retains the original complete target schema. Both
branches independently close; a stat modifier may retain its complete fixed
keyword grants. A separate fine capability requires the Kicker fact,
self-replacement and target-revalidation owners. The target is chosen while
casting and checked again before resolution; a copied spell may choose its
own legal target while retaining the paid fact. New target occurrences,
different counts, domains or predicates, divided quantities and private choice
results remain residual.

`card_programs/runtime_requirements.py` binds descriptor-specific requirements
in addition to registered handler minima. It retains the existing query,
announced-target-count and additional-cost requirements and validates both
branches and the shared target of schema 5 before runtime trust. Older
condition variants retain their existing runtime binding contract.

`compiler/public_replacement_rows.py` joins an adjacent default instruction and
its complete public-condition self-replacement only after the shared effect
compiler proves that pair. The node retains the original raw source span;
other keywords, casting permissions, and unsupported siblings remain separate.
Ability-word labels are removed only at top-level sentence boundaries. A
suffix such as “instead if” has the same typed condition and branch semantics
as a leading “If” clause. Exactly one branch executes, before external draw,
token, counter, or damage replacements.

Public same-target schema 8 retains the complete original target schema in
`target_binding`. Both branches independently close against that one role,
including its predicates, count, zone, and controller relation. The target is
chosen during casting and checked before resolution. Public source-result
schema 9 retains `source_binding`, validating symbolic source ownership before
runtime binding and the same resolved reference in each child afterward.
These variants use the existing public-condition choice owner and canonical
effect owners; older instruction schemas retain their original wire fields.
Copies evaluate public conditions for their resolving controller and current
game information, while preserving the copied effect and legal target choices.

The land-entry condition reads sealed entry events for the current turn and
their entry-time controller and card types. A searched land qualifies; playing
a land is not required. Another player's entry or a previous turn's entry does
not qualify. Graveyard conditions count cards, excluding tokens. A permanent
card matches the canonical six permanent card types, with each card counted
once; “permanent” is not a subtype. The resolving spell remains on the stack
while that condition is read, including when cast using Flashback.

Public self-death and attached-creature death returns bind the event card and
its exact post-departure zone-change counter. The existing graveyard-return
operation lowers that descriptor to the canonical zone move intent, which
skips missing or changed graveyard incarnations and returns to the owner's
hand. Departure attachment facts retain the dead creature when its Aura later
leaves. Optional, delayed and independently unsupported results remain residual.
The corresponding fixed battlefield return uses the same retained graveyard
incarnation and the existing reanimation operation. It returns under the
departed card's owner, optionally tapped and with one fixed +1/+1 entry
counter. Entry counters participate in the canonical replacement transaction;
the entering permanent's controller places them under CR 122.6a. Quoted
death abilities use the recipient's departure facts. Other controllers,
dynamic counters, attachment and transformation riders remain residual.

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

`compiler/kicked_targets.py` lowers a fixed Kicker-dependent targeted result
beside an independently closed prefix. Its source binding retains each target
domain and an explicit different-role relation for “another target.” Casting
binds the conditional group's count to zero or one from the selected Kicker
price before ordinary target selection and tax pricing. Unsupported target
replacement, variable counts, source-linked amounts and unrepresented sibling
results remain residual. Complete target-role closure is checked independently
from the compiler's declared capabilities.

`compiler/kicked_target_replacements.py` represents a complete fixed target
predicate or count replacement. Both the original and kicked result compile
independently with their own full target schemas. The selected cost determines
which schema is used before choosing targets, and the version-eleven cast-fact
instruction executes only its matching result program. Exact fixed target
sets retain their existing distinctness and partial-resolution behavior.
Variable counts, linked result values and unrepresented siblings remain residual.

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

`compiler/event_player_choice_templates.py` reuses that sacrifice leaf for
closed occurrence-player subjects. The established event-player binder chooses
the damaged player, sealed defending player, or departed creature's controller;
it does not replace them with the trigger controller. The effect origin stays
with the trigger while its APNAP player list independently addresses the chooser.
These references introduce no target, so the affected player selects a current
controlled permanent regardless of ownership. The source-self death owner uses
the same last-known controller table. Shape and program validation require the
corresponding normalized event facts; unrepresented current/LKI controller
reads and ambiguous antecedents remain residual.

`compiler/affected_player_discard_templates.py` owns the parallel mandatory
fixed affected-player discard leaf for one, two, or three cards. The same
target-player, target-opponent, each-player, and each-opponent relations feed
the existing APNAP selection owner, but hand candidates and prior selections
remain actor-private until one simultaneous replacement-aware move publishes
the actual destinations. Spell, triggered, activated, and fixed Choose one
modal bodies share the descriptor. Random, optional, variable, all-hand,
revealed or qualified, linked-result, cost, cleanup, and keyword-specific
discard forms remain source-spanned residuals. A separate controller relation
permits one through four cards as a standalone instruction or as a leaf of the
closed fixed draw sequence described above. Sealed player-damage and scheduled
upkeep occurrences can bind an unqualified discard of one through three cards
to that affected player. The effect actor remains the triggering controller;
the affected player alone receives the private choice. A separate printed
controller life result retains its controller recipient. These productions use
distinct shape-gated capabilities and the existing APNAP choice and zone owners.
Random or qualified selections, variable quantities and linked discard costs
remain outside this scope. An insufficient hand discards only available cards;
an empty hand introduces no mandatory private choice.

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

Runtime ownership follows each modal branch's own instruction scope. An ordered
draw-and-entry branch validates its two-to-eight effects independently of the
outer modal ability, whose effect list may be empty. The typed mode schema also
derives the modal capability and every sibling's dependency without relying on
the producer's annotations. Unknown sibling operations and malformed mode
cardinality reject complete admission before an action is offered.

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

The same compiler module separately lowers one mandatory announced-X life
payment, one mandatory exile of X cards, creature cards, instant cards or
sorcery cards from the caster's graveyard, or one mandatory discard of X cards,
creature cards or land cards from the caster's hand. The source-spanned cost retains an
immutable typed X parameter, and the independently compiled result uses that
same declaration. The fixed-cost leaf grammar still rejects variable amounts;
the announced-X production owns their composition. Alternative or optional
variable costs, random or different-name selections, X sacrifice or
return, and unrepresented linked payments remain residual. A spell combining
this variable additional payment with Convoke, Delve or Improvise also remains
residual until a shared payment-resource contract owns the joint budget.
The hand form requires its separate declared-discard capability and the
ordinary discard-cost owner. Its private dependent selector excludes the
casting spell and reuses the canonical predicate, payment and replacement
journal. Price recognition does not admit an independently unsupported result.

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

Fixed Prototype lowers an all-zone, complete-card characteristic descriptor
through the existing ability component registry. Shared cast offers and
accepted proposals choose the alternate mana cost, color and size before
checking permissions, costs and targets. Prototype is a characteristic choice,
so an independently authorized alternative cost still applies. The stack and
resolving permanent retain the choice as copiable values; ordinary zone reset
restores printed characteristics on other transitions. Variable sizes, complex
mana symbols and independently unsupported siblings remain residual.

`compiler/keyword_nodes.py` owns fixed-mana Morph, Megamorph and Disguise
productions. They lower the turn-up cost to a typed all-zone runtime component;
the casting proposal owner separately supplies the face-down `{3}` creature-
spell alternative, suppresses printed costs and abilities, and retains
external cost modifiers that apply to the represented face-down spell.
Disguise retains its face-down Ward, while Megamorph places its counter before
turn-up triggers. The same descriptor later authorizes a controller-only
no-stack turn-face-up action through the current effective-keyword boundary.
Ordinary descriptors retain their original serialized shape. Schema-two
descriptors add fixed two-color hybrid symbols and reuse the existing immutable
mana-option expansion. Each currently payable combination has its own offer,
with its chosen cost bound into the action identity and fingerprint. Commit
revalidates that option and pays its complete requirements through the canonical
mana payer without a spell or activated-ability spending context. Pending load
and replay retain the same selection. Variable, monocolored hybrid, Phyrexian,
snow, nonmana, conditionally reduced, copied, granted, text-changed, multiface,
merged and residual turn-up families retain their source-spanned exclusions.

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
one closed kicked counter-plus-keyword entry replacement.
`compiler/nonmana_kicker.py` lowers fixed life, controlled-permanent sacrifice
or return, owned hand discard, and subtype-qualified untapped-creature prices,
optionally preceded by fixed ordinary mana. The complete em-dash cost stays
one keyword instance, including commas between its payment parts. The cost component
adds a server-authored optional total-cost branch only for a complete admitted
CardProgram. Its paid stack fact flows into immutable zone-replacement and
normalized entry snapshots. The entry component creates a nested replacement-
aware +1/+1 counter event and optionally grants Flying, First Strike, Haste, or
Trample through existing consumers. Each nonmana descriptor independently
requires its existing payment capabilities. Result siblings keep their own
whole-card admission boundary. Multiple, and/or, variable,
copied, granted, kicked-trigger, spell-rider, dynamic, and open entry families
remain source-spanned residuals.

`compiler/activated_mana_nodes.py` also owns four exact source-self zone-move
effects: graveyard to owner hand, graveyard to battlefield tapped or untapped, and a
battlefield Aura to owner hand. The typed descriptor replaces the parser's
default battlefield active zone with the represented origin and records the
destination, tapped state, source form, and complete-card policy. Only the
battlefield result requires the shared complete-card admission certificate;
the hand-return forms may remain independently executable on partial cards.
An exact activation restriction may follow these instructions when its complete
timing, per-turn limit, and conditions already match the typed activation
descriptor. The compiler strips that suffix only for result recognition and
retains the original descriptor, so controller-upkeep and sorcery timing share
the ordinary offer and command legality owner. Missing or conflicting timing
metadata prevents this lowering.
Separation preserves the entire preceding effect text, including its final
period and earlier sentences. Activation instructions are not part of the
effect; removing a represented instruction must not alter the result grammar.
`activation_restriction_composition.py` joins complete `and only` clauses from
the existing closed instruction grammar. It retains every public condition,
one timing restriction and one usage limit, regardless of clause order.
Unknown clauses, duplicate predicates or conflicting limits reject the whole
instruction. Plain `once` carries a distinct once-per-object limit; it does not
share Exhaust state. Fixed own instant-or-sorcery cast and opponent life-loss
conditions lower to typed current-turn facts with their own runtime dependency.
Current public enchantment and other permanent-type existence checks use the
same closed object query as the existing artifact, creature and land forms.
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
Qualified battlefield targets reuse the shared full permanent predicate.
Owned graveyard cards reuse the closed graveyard-card query and may move to
the owner's library top or bottom. The complete origin, ownership, predicate,
and incarnation remain resolution-time checks. Live registration promotes
these programs only through the same closed shape used by CardProgram
admission; private library projection does not publish the ordered contents.
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

`compiler/untap_step_templates.py` lowers mandatory self untaps on other
players' turns and fixed untap prohibitions against nonbasic lands, legendary
creatures, represented subtype sets, inclusive fixed power thresholds, or named
counter minima. The untap-specific numeric grammar reuses immutable object
queries without widening continuous characteristic generators. Color unions
use one current controlled set; attached subjects retain their stated type.
Current untap components carry ability-fragment presence, so ability removal
suppresses their participation. Historical programs retain their recorded
coverage. Choice-dependent, variable, unrepresented conditional and compound
subjects remain residual.

The source-only optional untap clause lowers to a separate component in the
existing static untap participation family. `untap.step.optional_source`
authorizes the active controller's retained zero-or-more choice. The planner
and physical untap coordinator own participation and simultaneous untapping;
optional additional untaps on other players' turns, wider optional queries,
and phasing execution remain outside this optional-source contract.

`compiler/untap_selection_templates.py` lowers fixed zero-through-ten limits
over permanents, artifacts, creatures, lands, or nonbasic lands to
`untap.step.fixed_selection_limit`. Global limits and limits during the source
controller's own step share the existing participation registry and planner.
The optional untapped-source condition reads the source before any untapping.
Current ability fragments govern component presence. Variable bounds and
other-player-step limits remain residual; historical generic limit descriptors
retain their fail-closed execution path.

`compiler/public_state_queries.py` owns the shared fixed battlefield-query and
public-state condition grammar consumed by continuous characteristics, typed
queried ability grants, resolution-locked characteristic sets, and untap-step
participation. It accepts only battlefield sets representable by one immutable
`ObjectQuerySpec` over a source-controller, source-opponent, or global relation.

`compiler/public_condition_surfaces.py` owns closed source-language aliases and
fixed condition-subject predicates. Contracted graveyard presence, canonical
permanent self-descriptions, and equivalent other-creature entry wording reuse
the existing condition codecs and history facts. Colorless creatures, fixed
nontoken color qualifiers, and nonbasic lands retain every query restriction;
basic land subtypes do not substitute for the basic supertype. Runtime owners
receive only the compiled descriptors.

Land-condition quantities reuse the canonical land-subtype vocabulary and
the snow supertype. Explicit repeated articles in a controller disjunction
form separate closed predicates, preserving source exclusion in each branch.
Desert battlefield/graveyard alternatives and snow-qualified attachment
conditions use the existing flat Boolean codec. Current graveyard-card
conditions emit the existing version-two card-only quantity, excluding tokens
and noncard copies; historical quantity and condition decoders retain their
earlier shapes.

Live query and attachment keyword grants may carry fixed typed combat and
spell-cast trigger fragments for already-represented keywords. The shared
keyword codec binds each label to its exact fragment and consumer capability;
the runtime rejects missing, duplicated or mismatched fragments. Separate
grant sources retain separate trigger instances. Granted Prowess contributes
only instances beyond current printed Prowess programs, locks the recipient's
incarnation and trigger controller at the cast event, and uses the existing
ordered stack and temporary-characteristic owners. Later loss of the grant
does not remove an already-created trigger.
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

`compiler/attached_protection_exceptions.py` owns the closed source-Aura
retention sentence. Fixed colors and fixed protection from enchantments lower
through the existing attached-characteristics handler with a schema-v3 source
marker. The enchantment-type form may retain a signed power/toughness modifier
and declares its own additional capability. A separate source-chosen-color
component reads the existing public entry designation. These productions
exempt only the granting Aura from its own protection instance during current
attachment retention; all-Aura, already-attached, and chosen-card-type
exceptions remain residual. Independently compiled sacrifice abilities keep
ordinary target legality before cost payment.

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
Aura-specific characteristic evaluator. Version-three restrictions add one
fixed maximum power, fixed maximum mana value, or absence of flying on a
creature. They delegate to existing numeric and keyword target predicates;
version-two serialized shapes and fingerprints remain unchanged. Modified,
attachment-derived, variable, relative, and compound restrictions remain
residuals. Cast offers, resolution revalidation,
nonspell entry, and state-based attachment legality all consume the current
effective source-pinned characteristic snapshot. Player Auras use the same
reciprocal attachment owner with an internal typed player identity and project
only the public seat.

`compiler/fixed_attachment_templates.py`, `rules/attachment_actions.py`, and
`compiler/fixed_attachment_keyword_nodes.py` own a separate closed attachment-
action family. It lowers one source Equipment entry attachment, one source Aura
reattachment, fixed-mana Equip restricted to a creature token, commander,
legendary creature, or one creature subtype, and the ordinary Living Weapon and
For Mirrodin! and Job select entry triggers. Resolution revalidates the source incarnation and
current target before delegating to the existing reciprocal attachment owner;
the keyword triggers first use the replacement-aware token owner and attach the
source only to the original Germ, Rebel or 1/1 colorless Hero. Additional replacement-created
tokens remain unattached. Multiple targets, other attachment source kinds,
broader Equip restrictions, modified keyword token definitions, and
replacements that multiply or alter the original token remain source-spanned
residuals.
Decorative flavor labels on Equip preserve their original source spans. The
complete fixed-mana Equip child is recognized by the shared typed grammar for
compiler classification and activation catalogs; labels add no payment,
targeting or timing permissions. Unsupported costs and restrictions retain
their residuals.
`compiler/nonmana_equip_nodes.py` lowers closed nonmana Equip through the same
typed activation catalog and attachment owner. Fixed hand discard, controlled
creature or artifact sacrifice, another nonland permanent sacrifice, life,
energy, and one source charge counter are paid before an attachment can
resolve. Target legality, sorcery timing, source incarnation, and any printed
once-per-turn limit remain ordinary activation constraints. The cost descriptor
requires its own runtime capability independently of keyword recognition.
Alternative and variable prices and unsupported companion abilities remain
residual.

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
corpus includes the fixed source Exploit result as a composing carrier; its
inner target effect retains the same independent grammar and shape checks.
For paired Overload and Cleave sources, the audit reconstructs each resolution
body from the retained printed source and checks the fixed price, program role,
body fingerprint, and alternative-cost capability before the ordinary target
grammar checks. A keyword declaration cannot become part of the leaf body or
hide a changed target or price from assurance.
The compiler census derives grammatical shapes and representative identities from
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
`compiler/all_kind_counter_doubling.py` is a separate primitive in the same
counter-resolution dispatcher. It represents one fixed, up-to, or unrestricted
permanent target set with “double each kind” semantics. The existing scalar
target owner preserves type unions, controller relations, and current public
predicates. Its registered all-kind handler uses the existing counter intent
with a closed quantity mode and no selected counter name. The canonical counter
owner snapshots every positive kind and count before submitting one placement
batch; zero kinds create no request. Existing quantity replacements remain per
object and kind. The complete result also participates in the existing payment
component owner, so a reflexive cost precedes a new trigger's target selection.
Player counters, arbitrary affected sets, variable factors, and linked attached
object results remain residual.
`compiler/quantified_counter_targets.py` lifts one complete scalar counter
result to a homogeneous exact, up-to, or unrestricted target set. It also
recognizes a closed “choose targets, put counters on each” form. The shared
scalar predicate owner retains subtype and current public-state qualifications,
including battlefield entry this turn. The version-two target-set payload
records one fixed counter amount or the existing named-count descriptor;
unrestricted cardinality uses the existing selected-target-count descriptor.
Resolution snapshots each legal target's current named count before quantity
replacements. A zero named count creates no placement request. Independent
target groups, divided totals, and unknown result
companions remain residual.
The positive colorless qualifier can conjoin one excluded card type through
the closed type grammar. “Colorless nonland permanent” lowers to the existing
`colorless: true` and `types_none: [land]` fields. Current effective colors
govern eligibility independently of printed mana symbols and Commander color
identity. Artifact lands still fail the nonland predicate, and a color change
revalidates the original target before its selected result resolves.

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
replaying a threshold after the ability returns. Closed static characteristic
productions also remove one decorative ability or flavor label before their
existing grammar runs, while retaining the original text and span. Solved,
Solve, Class, chapter and modal markers retain their semantic boundaries.
Unknown body grammar and quoted text without a separately exact inner owner
remain material. Pinned subtype vocabulary includes the irregular plural
"Werewolves"; a Wolf/Werewolf union selects each current recipient once.

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

Class-level variable, hybrid, Phyrexian, snow, nonmana, malformed, granted,
text-changed, direct level-setting, independently unsupported, and
cycle-sensitive dynamic characteristic forms remain residual.

`compiler/maximum_hand_size_nodes.py` also lowers fixed increases and reductions to the current
controller's or each opponent's maximum hand size. Its closed signed descriptor
uses the existing maximum-hand-size component registry. `maximum_hand_size.py`
adds currently effective adjustments to the player's finite base, clamps the
result at zero, and preserves an existing no-maximum permission. Source control,
departure, phasing, and ability removal change the live rule. Cleanup determines
the active player's exact private discard before end-of-turn ability or control
effects expire. Variable amounts, fixed set-to values, other player relations,
and temporary hand-size effects remain outside this grammar.

`compiler/day_night_nodes.py` and
`compiler/spell_history_transform_nodes.py` close one paired-face family.
Canonical Daybound and Nightbound keywords must occupy opposite faces of a
nonmodal double-faced card. The legacy grammar accepts only the two exact
"beginning of each upkeep" previous-turn spell-count conditions and a bounded
source-self reference. Both forms use the shared current-ability component
query, one bounded previous-turn summary, and the logical-object-aware
transform operation. Printed fixed self-transform activations now use that
same owner. Their ordinary activation costs are independently compiled, and
the activation stack item pins source incarnation and transform count under
CR 701.27f. Two queued activations with the same count can transform once;
blinked sources invalidate the old instruction. Other steps or thresholds, arbitrary Transform or
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
display text. Fixed creature tokens with Prowess carry exactly one canonical
`SpellCastKeywordTriggerSpec` beside the keyword label. Definition validation
and an independent runtime minimum require both the token-definition and
Prowess capabilities. The existing source-local trigger owner handles each
later noncreature cast by the token's controller; the creating spell and spell
copies do not retroactively trigger a new token. The fragment is copiable,
while its temporary power/toughness bonus is not. Ability loss suppresses new
triggers without cancelling instances already on the stack. Class self-entry
wording uses the same source-incarnation binder as other permanent self-entry
subjects. Independently unsupported creator clauses remain residual.
A predefined Blood definition additionally requires
`token.ability.fixed_blood`: its explicit activation profile retains all four
costs and delegates owned-hand selection, source departure and private draw to
existing generic owners. The token's name or display text does not select its
runtime ability. Lander and Mutagen have separate predefined-profile capabilities.
Lander preserves a private basic-land search, tapped entry and mandatory shuffle,
including legal failure to find. Mutagen retains sorcery timing and one current
creature target for its fixed counter. Both pay their complete source costs
before an independent queued result. The same owner lowers canonical fixed Investigate, fixed
Afterlife through permanent-graveyard LKI, one current-target copy token, and
one source-independent next-end-step creation. `compiler/token_copy_templates.py`
adds fixed copy recipes referencing the source, one qualified public permanent
target, or a represented zone-event object. Copiable fixed exceptions retain
nonlegendary, base-stat, color, subtype, card-type and keyword distinctions.
Version-two exceptions additionally remove the mana cost and set mana value
to zero while preserving version-one serialized forms. Fixed ordinary-mana
Embalm and Eternalize use canonical graveyard self-exile costs and these copy
recipes, with an independent complete-card admission marker. The original
pre-cost source snapshot supplies the token after source exile. Embalm creates
a white Zombie copy; Eternalize creates a black 4/4 Zombie copy. Other printed
characteristics and supported abilities are retained. Nonmana keyword costs
and unsupported siblings remain residual.
Quoted granted abilities use the existing typed-token child programs; recurring
copied cleanup is not converted to an independent delayed trigger. Normal
Populate uses the existing token-copy choice owner without the historical
Haste-and-sacrifice rider. Every form reaches the existing replacement-aware
`token_creation.py` transaction. Incubate, Roles, attached or attacking tokens,
linked, chosen, hidden, arbitrary graveyard or spell copy origins, face-down copy values,
arbitrary quoted abilities, unsupported keywords, other delayed times, and
independently unsupported compound or conditional instructions remain
source-spanned residuals or explicit unsupported-interaction boundaries.

## Invariants

Fixed keyword producers use the ordinary and qualified Landwalk consumers and
the existing Horsemanship owner. Live queried and attached grants use current
recipient relationships, while resolution-locked grants retain their existing
incarnation and duration rules. Missing provider keyword indexes do not erase
an exact trusted printed qualified-Landwalk declaration. That fact enters the
ordinary base-characteristic pipeline before ability removal, face-down
changes and copiable-value capture. Chosen or varying landwalk types,
unrepresented compound predicates and other as-though domains remain residual.

Fixed Landwalk block permissions compile as source-owned rule fragments.
Named variants select that exact keyword, while all-Landwalk wording selects
the represented family. They leave every ability present and alter only the
Landwalk check for a proposed block. Global sources apply to every blocker;
attached-blocker sources require the current reciprocal attachment identity.
A fixed attached stat modifier may share the same complete clause without
granted ability text. Source ability loss or departure ends its rule, while
recipient ability loss does not remove an Aura-owned rule. Other evasion,
restrictions, requirements and costs retain their own owners.

`compiler/soulbond_nodes.py` lowers Soulbond to two independently identified
entry triggers, including repeated instances and printed keyword siblings.
Their intervening-if checks and optional untargeted choices retain the original
source, entering incarnation and trigger controller. `soulbond_templates.py`
lowers complete paired keyword/P-T clauses and separately exact quoted children
through the existing grant compiler. The paired runtime component applies each
source's modifier to both current incarnations through an explicit paired
relationship. Unknown paired procedures and inexact children remain residual.

Fading, Graft and Vanishing lower their separate entry and lifecycle abilities to
the current counter and trigger owners. Graft emits a non-targeted optional
counter-movement choice with the intervening source-counter condition and
pinned source and entry identities; recipient replacements complete before
the shared counter owner commits removal and placement. Vanishing retains its intervening-if
upkeep and separate last-counter sacrifice trigger; ordinary counter removals
observe post-event characteristics before stabilization. Both cleanup actions
carry an explicit sacrifice transition through the ordinary zone-move intent
and canonical replacement transaction, preserving sacrifice occurrences and
history even when a destination replacement prevents death. Umbra armor lowers
an Aura-owned current fragment and requires the complete coupled destruction
capability. Current relationship tracking and compiler recognition alone do
not make a card supported. See [ADR 0107](../adr/0107-typed-coupled-umbra-destruction.md)
for replacement choice, recursive Aura destruction and replay ownership.
The shared destruction snapshot preserves distinct errors for missing objects,
phased-out or nonbattlefield subjects, and malformed effective keywords.
Preparation rejects each case before committing a destruction transaction.

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
A complete fixed source "deals damage to you" clause lowers to ordinary
nontarget damage bound to the resolving controller. It retains the original
source attribution, requires the existing positive-amount and player-damage
capabilities, and does not declare a target or acquire a target-selection
window. Ordered spell bodies, source entry/upkeep results, and independent tap
triggers reuse the same leaf. Unknown recipient references and condition tails
remain residual.

Fixed mana results use a separate version-two handler while ordinary output
descriptors retain their version-one serialized identity. One complete fixed
output may be followed by fixed source damage to the activation controller or
an original-source restriction through that player's next physical untap step.
The compiler consumes the entire result clause and emits a closed typed value.
Unknown tails, targeted or conditional results, and source-sacrifice result
compositions remain residual. Explicit activation and automatic payment consume
the same selected mode and result. Damage uses the ordinary replacement and
prevention transaction; the untap duration uses the player-locked canonical
tap-state owner without publishing an Exert occurrence.
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
positive-count Suspend, plus bare Aftermath, Jump-start, Rebound, and Retrace, lower
through one typed cast-lifecycle owner. Every descriptor declares both public
and zone-cast capabilities required by the registered shared handler, plus its
kind-specific cost or combat dependencies. Mayhem adds a version-four fixed or
bare descriptor within this same registry. Current owner-graveyard permission
requires an exact adjacent discard incarnation in the current turn journal.
Fixed costs use the alternative branch; bare declarations use printed spell
costs or normal land-play timing and limits. Mandatory additional costs,
current-ability revalidation and ordinary destinations remain authoritative.
Hybrid, variable, snow, Phyrexian and nonmana Mayhem costs remain residual.
Every prior descriptor retains its original version and serialized shape.

Buyback also has a version-five typed nonmana price. Its compiler recognizes
fixed selected hand discard, controlled land or basic-land-subtype sacrifice,
fixed life payment, and fixed life followed by one server-selected random
discard. Runtime validation reads the immutable price descriptors; Oracle and
cost text remain display metadata for this variant. The existing optional-cost
expander retains base additional prices, and the canonical payment owners pay
the chosen costs before resolution. The paid lifecycle uses the existing
owner-hand replacement, while countered spells and fresh normal casts keep
their ordinary destinations. Independent descriptor minima require the new
Buyback and composed life/random capabilities.

This conservative descriptor
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

Aftermath binds only its selected split-card half. It grants owner-graveyard
casting and prohibits that half from all other zones, including under another
casting permission. Every actual graveyard cast retains its mandatory
stack-departure exile context even when a separate permission waives the mana
cost. An uncast spell copy receives no such replacement. Split and modal
double-faced spell offers use distinct face identities and the selected face's
printed cost, colors, mana value, type and result program. Their prospective
card views are read-only; ordinary zone objects retain their own characteristics.

The fixed single-target reanimation shape joins the existing closed effect
promotion predicates. Compiler recognition and capability closure therefore
reach ordinary trusted runtime registration through the same validated target
and zone owners; no arbitrary reanimation program is promoted.

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
Each represented nonmana payment also declares the shared fixed payment
owner it invokes. Alternative-price eligibility cannot substitute for the
sacrifice, life-payment, discard, exile, or return authority used at commit.

Conditional “without paying its mana cost” forms reuse that owner with an
explicit version-two condition. The closed grammar requires either one
caster-controlled basic land type and one opponent-controlled basic land type,
or any designated commander currently controlled by the caster. The commander
may be owned by another player. These forms add one zero-mana replacement price
while preserving the normal printed price, ordinary timing, targets, cost
increases and atomic payment. Current effective land types, control and phasing
govern eligibility; later changes do not reprice a committed spell.
Fixed source-only temporary stat changes use the existing incarnation-bound
characteristic instruction. A source-equivalent reviewed price/exile compound
is superseded only when source hashes, target, fixed effect and the represented
commander price agree with the current typed programs.

Battlefield sources can separately grant a fixed alternative mana price through
`GlobalAlternativeCostSpec`. Its source-spanned descriptor retains a fixed
spell type/subtype selector and an optional hand-only restriction. Current
controller, face, ability presence, and source incarnation govern availability.
The grant adds one alternative before ordinary additional-cost expansion and
price modifiers; it preserves optional prices, mandatory costs, commander tax,
and increases or reductions. The selected spell characteristics govern the
selector. The grant supplies no casting-zone permission and cannot combine
with a forced alternative method. A printed mana-cost X is zero when the
alternative contains no X. Private or dynamic selectors and nonmana or variable
granted prices remain residual.
Optional additions retain the selected base alternative identity through
lifecycle validation, including paid nonmana Buyback and its resolution return.

`compiler/turn_cast_grants.py` lowers a complete once-during-own-turn casting
permission into the existing cast-cost component registry. A closed source can
grant an ordinary-cost cast from its controller's graveyard or an optional free
base from hand or the current library top. Public spell predicates retain
their exact type, subtype and exclusion meaning. The normal hand cast and
independent zone permissions remain separate choices. A source-bound grant
price preserves additional costs, Kicker, taxes and reductions; only printed
mana X is zeroed by a free base. Selected face-down characteristics govern
qualified spell permission. Unrepresented linked results, extra payments,
arrival qualifiers and shared land-use clauses remain material residuals.

Version-two grant data also represents a fixed follow-up on an ordinary
graveyard cast: exile the original spell instead of putting it in its caster's
graveyard, or have the granted artifact enter tapped. Separate registered
handlers require the corresponding fine-grained capability; the original
schema-one descriptor remains unchanged. The chosen cast binds the follow-up
to its stack incarnation. The existing zone-replacement snapshot receives an
ordinary replacement candidate, so ordering and entry-copy choices remain with
the canonical coordinator. A later source departure does not erase it, while
the spell's zone change ends it. Extra payment and other linked clauses still
require their complete owners.

Version-three grant data adds exact current-incarnation graveyard qualifiers.
A Mill-qualified spell permission reads the committed Mill-result journal;
generic library-to-graveyard moves such as Surveil do not satisfy it. A shared
land-or-permanent permission instead reads ordinary library-arrival history and
uses one source allowance across both actions. It supplies zone access without
adding a numeric land play. Dedicated registered qualifier handlers require the
matching capability and preserve schema-one and schema-two serialization.
A separate qualifier accepts a current owned creature incarnation arriving
this turn from any zone other than the battlefield. It reads the exact
committed origin; battlefield deaths and prior incarnations do not qualify.
An ability-word prefix has no additional meaning and is removed only when the
entire supported permission body remains exact.
Functional Visit and Case Solve/Solved markers require their own semantics;
generic decorative-label normalization keeps unrepresented forms residual.

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
`$source.zone_object`, a revalidated direct permanent target, or the typed
current-or-last-known creature attached to the source. Direct targets reuse the
shared closed predicate grammar for subtype unions, colors, controller
relations, and current named-counter membership. A bare creature subtype does
not require the Creature card type; a matching noncreature Kindred permanent
is eligible. The full target predicate is revalidated before shield creation.
Exact direct-target and fixed-set destruction may carry an immediately
adjacent cannot-be-regenerated rider as a flag on the same destruction effect.
Cost, event, target-predicate, and sibling-effect compilation remain
independent, so unsupported surrounding grammar stays residual even when the
fixed effect sentence matches. Static, variable, repeated, optional,
conditional, unrepresented predicates, multiple-target, damage-linked,
delayed, and linked-result forms remain outside this family.

Public mass-damage lowering uses the same complete `ObjectQuerySpec` descriptor
consumed by the runtime affected-set snapshot. The compiler emits ordered
player/permanent groups and an optional exact target-opponent controller; it
does not encode card names or reparse Oracle text during resolution. One
independently closed public permanent set may join all players or all
planeswalkers in the same damage instruction. A qualified creature set retains
its predicate, and an overlapping creature-planeswalker is one recipient. The
existing amount owners supply the same fixed, announced-X, or independently
closed public-query amount to both groups. Membership uses current public facts
when the instruction resolves, so
a prior flying change matters; these public sets do not target their members.
Only predicates accepted by the typed damage-set model are admitted.
Target-dependent second groups, extra or repeated groups, divided damage,
unknown amount links and unrepresented result riders remain residual.

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
effects are added. Bare creature subtypes constrain the subtype independently
of card type, so matching Kindred permanents and cards participate in their
represented public-zone counts. Explicit Creature card-type predicates retain
their separate type constraint. Represented type and color changes feed the count without
later-layer recursion. Compilation reuses the shared fixed battlefield grammar when the earlier count grammar
does not match. Subtype unions count an object once; explicit subtype exclusions
remain independent of the creature type constraint. Named counter and modified
state predicates use the existing static quantity form. Attachment facts retain
the enclosing evaluation's ability-applicability boundary, include only current
phased-in attachments, and make an Aura controller-relative for modified state.
Complete nonlegendary-creature and nonbasic-land descriptions reuse the existing
version-eight supertype exclusion in public battlefield queries. The same
predicate feeds fixed modifiers, keyword grants, characteristic definitions
and public result amounts. Basic is a supertype: a nonbasic land can have basic
land types, and changing those types does not make it basic. Existing complete
controller, source and state qualifiers retain their fields; unrepresented
negative qualities, hidden predicates and compound grammar remain residual.
Keyword and numeric-characteristic predicates remain outside static counts.
Current compilation uses the source's current controller
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

Resolution and mana instructions may instead carry an explicit
`LiveBattlefieldQuantitySpec` through the existing amount codec. The compiler
reuses the fixed public battlefield query grammar for current attacking or
blocking objects, current keywords, and source exclusion. The read-only count
uses current characteristics when that instruction applies and retains its
locked controller; it does not widen static characteristic quantities.
Attachment-state, numeric feedback, hidden identities, and unsupported query
forms remain residuals. The live count has its own required producer capability.
Counted token instructions retain supported trailing keyword abilities as part
of the complete fixed token definition before lowering the separate quantity.
The existing token compiler still rejects unrepresented abilities and definitions.

Fixed additional-token replacements reuse that same complete definition owner.
Their creator/controller frames accept one fixed addition, retain artifact,
creature, or Treasure filters, and apply once per creation event. Predefined
profiles keep their canonical descriptors; other fixed creature definitions must
fit the existing additional-token codec without separate creation riders.
Optional additions, variable amounts, unsupported definitions, and extra result
instructions remain residual.

`compiler/recipient_modifiers.py` lowers fixed modifiers calculated separately
for each creature in a closed battlefield set. Its typed scalar reads that
recipient's current colors, fixed named counters, colored symbols in its
copiable mana cost, or distinct creature types with an optional fixed maximum.
Singular `each` subjects retain their qualifiers and source exclusion through
the existing public query owner. Complete ability and flavor word bodies keep
their source spans; mechanical scope markers and unrepresented conditions
remain residual. Power/toughness feedback, private collections, and extra
unrepresented result clauses are outside this grammar.

`compiler/shared_type_quantities.py` uses that same recipient operation for
fixed modifiers counting other creatures with a shared creature type. Its
closed subjects are every creature or the source's actual enchanted/equipped
creature. The count covers the battlefield or the modifier source controller's
creatures. The runtime captures current public type facts through the existing
layer-five resolver, then compares each recipient after earlier copy and type
layers. A creature counts once even when several types match. The operation's
version-two payload retains typed member incarnations; version-one local
quantity payloads keep their serialized fields. Shared colors or names,
different reference objects, and unrepresented result clauses remain residual.

`compiler/declared_effect_amounts.py` binds ordinary announced cost X or
one complete public count definition to numeric result slots in existing typed
instructions. Mandatory `mill cards equal to` wording normalizes to the same
explicit public definition before the existing quantity owner runs. It retains
the printed player subject and reads one current public count at execution;
optional mill and unresolved quantities remain residuals.
Two fixed-value compilations must retain identical operations,
target schemas, and dependencies; only supported result fields may vary. The
original fixed template identity is retained for runtime admission. Public
declarations carry explicit Oracle-node-scoped identities and persist their
first execution-time value across sibling results and replacement resumption.
Creature-token definitions accept the singular articles `a` and `an` as a
count of one. This also preserves the literal projections used to validate an
`X/X` definition without changing the original source span. Resolved power and
toughness become the created token's numeric base characteristics, with a
separate token quantity; they are neither counters nor a live definition.
An explicit creature definition payload independently requires its existing
token-definition capability. The canonical Afterlife definition retains its
specific producer requirement. Copy recipes and legacy references without a
definition retain their separate ownership paths.
The complete create-token/put-counters followup production preserves those as
separate instructions. Only the closed token definition is created first;
the following counter amount stays deferred until the actual creation results
are bound. Its own capability requires the token and counter owners, including
replacement-aware placement. Unsupported attachment and linked-result clauses
remain residual. Announced X also retains ordinary colored-hybrid mana choices;
the represented casting-cost owner still validates and pays the selected price.
Independent quantity expressions retain their separate execution-time reads.
One fixed counter placement may consume that existing public-query amount
beside independently closed ordered components. The compiler preserves the
original placement subject, counter kind and target schema; the canonical
placement owner receives the concrete execution-time integer. Unrepresented
activation costs still block an otherwise recognized result.
The casting and activation owners choose and pay their respective cost X.
One represented mandatory additional life, owned-graveyard exile or hand-discard
price also supplies spell X when the printed mana cost has no X. The compiler
retains the existing typed stack-X expression and target-count descriptor.
Promotion checks the entire price and then the independently owned result;
it does not remove an unrepresented sibling. Ordinary mana-X restrictions,
unsupported nonordinary X mana, ambiguous definitions and multiple unowned
price contexts retain their rejection boundaries. Fixed mana stays fixed;
waiving it does not waive the additional price or force that price's X to zero.
`compiler/activation_x_costs.py` admits ordinary unrestricted battlefield
activation mana costs with one or more X symbols, optionally a source tap.
The closed catalog retains the symbol count, while the existing declared
amount projection varies only numeric results and preserves fixed target
domains. Discovery requires the existing exact complete-card certificate,
including when the catalog is attached after effect-program generation.
An exact leaf beside an unknown sibling cannot expose a newly paid X action.
`compiler/activation_cost_modifier_templates.py` lowers complete fixed generic
activation taxes and reductions, including their per-effect minimum sentence,
into the existing cost registry. Controller and enchanted-source relations
remain separate from the shared public object predicate; nonmana exceptions
stay explicit. Runtime discovery also requires the source's current layer-six
component, so removal, control changes and source departure affect subsequent
prices. Version-one descriptors retain fixed prices; separate versioned
extensions own represented targets and public quantities. Unrepresented values,
history qualifications and source zones remain residual. Recognition does not remove unrelated
Companion, return, Adapt or other whole-card residuals.
Quantified target characteristic clauses reuse the existing schema-6
target-set owner. The compiler converts the plural result to its singular
template without dropping a conjunctive keyword grant, and the shape owner
uses canonical keyword mechanic IDs. Unrepresented result clauses remain
residual. Payload-derived runtime requirements retain the set and keyword
authorities through ordinary, activated, triggered and modal instruction
wrappers, while existing quantity owners validate composed scalar amounts.

Quoted static characteristic children use the existing attached, query and
conditional grant shells. Only closed attached keyword/stat modifiers and fixed
public stat anthems produce version-three static references. Attachment words
inside those children refer to the recipient; the ordinary external-reference
rejection remains for other quoted abilities. An Equipment-qualified condition
binds the attached permanent without adding a creature predicate. Grant time is
bound by the continuous effect, and descriptors cannot prebind it. Nested
grants, type/color changes, dynamic quantities and unsupported siblings remain
residual. Parent and hidden child both require the characteristic-grant owner.

Public source-power costs use an explicit version-four metric through the
existing cast and activation payment owners. The compiler binds only a
represented self reference and complete public spell or activation subject;
mana-value bounds remain predicates on the selected spell. Version-three
activation modifiers additionally carry a closed public multiplier. Controlled
Dragon permanent counts apply only to validated Boast identity, including
Kindred permanents. Provider-power and public-quantity capabilities are inferred
from executable descriptors, so removing their declarations blocks promotion.
Older metric, modifier and handler schemas keep their serialized forms.

`compiler/targeted_spell_costs.py` lowers complete source-face conditional
generic reductions to a sealed public target predicate in the existing cost
component registry. Battlefield permanent predicates, public graveyard
creature-card mana values, and creature or blue spell predicates retain their
separate target domains. The compiler does not turn private zones, nested
antecedents, colored reductions or unresolved effect siblings into support.
Announced-cardinality recognition safely declines sequence components that
lack its required whole-ability event and identity context.

Printed Increment uses `compiler/increment_nodes.py` and independent
source-spanned instance identities. The normalized cast event carries the
actual committed mana total in explicit version 7, copied from the existing
paid stack provenance after costs and modifiers. `increment_model.py` owns the
strict greater-than-power-or-toughness comparison; the rules condition owner
checks the current creature and source incarnation both at discovery and at
resolution. Counter placement and replacement use the existing transaction.
Mana value, free casts, copies and controller identity remain distinct;
unsupported sibling instructions retain whole-card residuals. Event schemas
1 through 6 keep their original fields and serialized payloads.

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

Spell and permanent additional-cost composition admits mandatory homogeneous
selected zone-change prices of two through ten objects through the existing
counted-cost descriptor. Binary alternative prices can contain those same
counted leaves. The isolated single-object leaf retains its narrower boundary;
whole-card composition opts into counted normalization. Distinct eligible
selections, the spell's exclusion from its own price, simultaneous zone-cost
commitment, replacement continuations and private hand projection remain owned
by the canonical casting transaction. Variable, random, optional and mixed
prices remain residuals, as do independently unsupported result clauses.
Sequential replacement choices retain earlier selected-object cost journals
across transactional replay. The continuation decoder admits only distinct
selected refs with prior canonical event identities; the casting owner still
revalidates every payer, zone, predicate and selected replacement at commitment.

`compiler/random_discard_spell_costs.py` separately admits one mandatory
unrestricted random hand-card price. Its closed descriptor requires
`casting.additional_cost.fixed_random_discard` alongside the existing random
discard and ordinary discard-cost owners. Promotion validates that price and
then the independently supported result; recognizing the price does not admit
an unsupported result. The server excludes the source spell, pays ordinary
mana first and selects one other current hand card through the canonical random
owner. Multiple, qualified, optional and compound random prices remain residual.

`compiler/random_graveyard_returns.py` lowers untargeted random returns from
the resolving controller's own graveyard. Fixed counts and independently
declared X retain their existing quantity authority; represented card types,
type unions and one creature subtype remain closed selection data. The rule
owner queries the current eligible public set when the instruction applies,
samples distinct physical identities through the shared instruction-keyed
selector, and delegates movement to the canonical simultaneous zone owner.
The selector preserves historical random-discard seeding and ordering.
Known zero, empty and short pools are legal; random cards are not targets or
player choices. Extra graveyard ordering, different destinations and
independently unsupported siblings remain residual.

Global fixed characteristic sets accept canonical creature-subtype plurals with
optional `All` and `Other`. The shared battlefield query carries no controller
restriction and excludes only the source when `Other` is printed. Both fixed
stats and keyword grants consume that same predicate. Unknown nouns, open union
clauses and Class level context retain their existing fail-closed boundaries.

Commander-creature characteristic sets use the version-six designation field
in the existing object query. The field reads the authoritative physical-card
designation and current creature type; it is not a subtype or copiable value.
`you control` binds the source controller as recipient controller, while
`you own` binds that player as recipient owner and includes borrowed commanders.
Stats, keywords and independently closed quoted abilities use the same live
query and require `state_query.commander.designation`. Prior query versions keep
their exact serialized shapes; unrepresented quantity and cost grammars reject
the new designation qualifier.

Split second lowers to a trusted stack-local static descriptor in the existing
cast-rule registry. The shared casting and activation availability owners read
current spell declarations for every player; they block spells and nonmana
activations while retaining mana abilities, triggers, special actions and
priority passes. The restriction remains active beneath triggered abilities
and on copied spells, and disappears when its last stack source leaves.

Each printed Extort instance lowers to an independently capability-closed cast
trigger. The typed choice owner offers one white or black mana payment or
decline. It delegates simultaneous opponent loss and later actual-total gain
to existing life transactions, preserving replacement choices, locked trigger
control, source departure, pending save/load and replay. Multiple instances
retain separate payments. Unsupported outer grants and whole-card siblings
remain material residuals; the ownership decision is in
[ADR 0113](../adr/0113-typed-extort-and-life-loss-batches.md).

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
Undefined X, X-dependent target domains or cardinalities, unrepresented variable
activation costs, nonordinary X mana costs, linked or source characteristics, open arithmetic,
unrepresented result owners, and nested modal declarations remain residual.
Ordinary Cycling explicitly
declares the existing draw handler dependency beside its activation capability;
runtime binding still rejects undeclared registered dependencies.
The same public-query amount boundary supplies a resolved controller Scry or
Surveil count before the private library partition is prepared. An inline Scry
definition followed by a fixed draw uses the same declared quantity binding
and ordered controller sequence. Zero creates
neither a choice nor a completion event. Positive instructions on an empty
library still complete and can produce their normalized player-action event.
Scry and Surveil observer predicates retain controller, opponent, or global
scope without exposing looked-at identities in the public occurrence.

Printed fixed ordinary-mana Transmute uses the existing activation catalog with
complete-card admission, a hand-only discard cost, and canonical sorcery timing.
The activation owner captures the source incarnation and mana value before the
discard transition. Resolution reads that retained information and delegates
the equal-value private library search, public result reveal, hand movement,
fail-to-find choice, and shuffle to the existing search owner. Source departure
or a public discard replacement does not change the retained search value. An
unrevealed discard redirected into a hidden zone leaves its characteristics
undefined; the canonical search permits looking and shuffling while finding no
card with that undefined quality. Variable,
hybrid, Phyrexian, snow, nonmana, and granted Transmute remain residual.

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
`compiler/query_counter_sequences.py` composes these independently closed
quantity instructions in printed order, including a fixed life instruction
followed by a signed public creature-set modifier. Fixed-value probes preserve
the prior fixed owner before trying the complete closed atomic grammar.
Each explicit definition keeps its own instruction binding. A later quantity
reads after earlier instructions and their replacements commit; suspended
state actions do not repeat the earlier life change. Unknown clauses and
ambiguous cross-instruction object references remain residual.
Source attacks, blocks, and becomes-blocked results may use a source pronoun
with a complete fixed keyword/modifier body or an independently closed public
characteristic scalar. The child result retains its original source span and
full capability closure; target, created-token, and event-object pronouns do
not become source references.
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
represented current-turn definitions. Mandatory `mills cards equal to` and
explicit-X mill definitions share the same result projection. Source attack
triggers read the referenced creature's current characteristic, or its immediate
departure information for the same incarnation, while retaining the locked
trigger controller and one amount across an opponent batch.
Its fixed-value differential proof
retains the operation, target schema, and dependency owner; only result slots
receive scalar data. Explicit source references, singleton direct battlefield
targets, and represented zone-event objects remain distinct origins. Ambiguous
pronouns, optional target references, X-dependent targets or costs, aggregate
extrema, chosen facts, and independently unsupported bodies remain residual.
Declarations receive Oracle-node-scoped identities; separate declarations
cannot share a cached value. Ordinary negative result amounts use zero before
the printed result sign, while known zero and unavailable information remain
different outcomes. Doubling/base-setting exceptions are outside this grammar.

`compiler/target_hand_amount_templates.py` adds an explicit version-4 scalar
origin for the current hand size of one directly selected player. The source
and runtime shape owners require the complete one-player target schema and a
separate fine capability, together with scalar and target-revalidation
authority. The resolver reads public cardinality when the instruction is
applied, after responses; it never reads private card identities. An explicit
declaration also binds the selected seat. Known zero remains distinct from an
unavailable player, and an illegal sole target prevents resolution. Caster
counts, quantity-only targets, event-player inference, private predicates,
per-player group arrays and hand-count arithmetic remain outside this slice.
Earlier scalar versions and declaration identities retain their wire contract.

`compiler/multikicker_results.py` links complete count-result clauses to the
single printed fixed-mana Multikicker instance. Its version-5 scalar carries
the typed cost identity and fingerprint, and delegates draw, life, damage and
token consequences to existing result owners. Counter/unless plus count-draw
composition uses the existing fixed-clause owner after projecting only the
linked result slot. This extension does not admit other scalar sequences.
Variable target cardinality, private count-discard choices, multiple cost
instances and unsupported sibling clauses remain residual.

`compiler/mana_color_result_nodes.py` lowers complete ordinary spell clauses
whose declared result is the number of colors actually spent to cast that
spell. Version 6 uses the existing public `StackItem.mana_colors_spent`
provenance and existing result owners. Additional and alternative costs count;
colorless mana, unspent pools and other spells do not. A spell copy has no
casting payment and evaluates zero. Fixed suffix instructions keep their order
and consequences even when the variable amount is zero. The typed reader is
restricted to spell resolution; source-entry and variable-target consumers
remain residual. Earlier scalar encodings are unchanged.

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
sacrifice, source-token damage, chosen and modified forms remain residual.
Singular source-qualified damage subjects reuse the sealed type, subtype union,
supertype, and color predicates of the batch owner. Each qualifying source and
player recipient yields its own trigger; the one-or-more descriptor alone
coalesces that committed batch. Both retain combat and opponent restrictions
and current observer ability checks. Unsupported source state, token, chosen,
and ability-dependent qualifiers remain residual.
Repeatable sacrifice and discard subscriptions also compose with independently
exact counter-placement bodies. "Another" excludes the current source reference;
sacrifice predicates consume pre-event custody and characteristics, while typed
discard retains its cause after a visible destination replacement. Noncreature,
nonland discard requires both type exclusions. The existing current ability,
APNAP, source-incarnation and counter transaction owners execute the result;
no aggregate or per-turn limiter is inferred.

An already closed public subscription also composes with one mandatory fixed
positive source-counter instruction through
`counter.producer.fixed_public_source_event`. The result names the observer,
independently from the event object, and pins its battlefield incarnation.
Printed event/controller predicates, one-or-more aggregation and current-ability
discovery remain unchanged. A queued result survives later ability removal;
it skips a source that departed or reentered. Optional, variable, linked,
multi-effect and ambiguous event-object counter results retain their separate
admission boundaries.

`counter.producer.fixed_public_target_event` separately composes one mandatory
positive direct-target counter leaf with those closed public subscriptions.
The leaf retains its complete printed target schema, authentic controller
selection and ordinary target-incarnation revalidation. Source disappearance
does not cancel a legal independent target result; target blink or reentry
cannot substitute a new object. Optional, variable, multitarget and compound
results remain outside this producer.

`trigger.event.normalized_anywhere_graveyard` separately admits a physical card
entering own, an opponent's, or any graveyard from anywhere, optionally qualified
by one fixed public card type or color. The subscription consumes
`card.enter_graveyard` with destination characteristics and current incarnation;
it discovers only observers present after the committed transition. Existing
independently closed results, including optional draw, retain their own grammar
and capability checks. Tokens, replacement destinations outside the graveyard,
source-self shuffle, chosen qualifiers and one-or-more batch grammar are outside
this subscription. Death and battlefield-departure predicates continue to use
their distinct last-known-information events.

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
consumes the sealed last-known snapshot.
New Encore payloads seal the copied card's incarnation separately from the
acting ability source, preserving both original-source targeting facts and
current copy information after the exile cost. Older payload forms retain
their recorded identity path. Its per-opponent and replacement-
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
The unqualified source "blocks or becomes blocked" pair preserves independent
source conditions on both subscriptions. Its fixed signed P/T body uses the
existing source-incarnation and until-end-of-turn effect owner. The pair does
not infer a per-blocker "by a creature" occurrence or a qualified combat subject.
`card_programs/reviewed_overlay.py` owns the single precedence query shared by
CardProgram compilation and live generated-program registration. It removes a
reviewed split only when every subscription has one trusted, body-identical
source-self counterpart, preventing duplicate triggers without card identity
dispatch.
`card_programs/current_view_equivalence.py` also proves redundant fixed search
and controller-upkeep type-count life views. It requires matching nonempty
Oracle and ruling hashes, current capability closure, event and target scope,
and complete result semantics. A reviewed activated search with no cost schema
is redundant only when it retains the identical compiler-pinned cost catalog.
An upkeep type count preserves the exact controller, type, threshold and life
result while normalizing conjunction nesting and the redundant beginning phase.
Conflicting costs, conditions, results or source hashes retain the reviewed
view; current assembly never removes one merely because the IR is exact.

`compiler/static_cast_rule_templates.py` and
`compiler/activation_restriction_templates.py` own the closed battlefield-
static action-legality grammar. They lower fixed public spell queries into
instant-timing permissions, cast prohibitions, one-spell-per-turn limits, and
spell or ability counter prohibitions; the same family lowers fixed public,
chosen-name, enchanted-object, and enchanted-player activation prohibitions.
The exact enchanted-permanent untap/prohibition compound composes the existing
untap participation capability with the activation restriction instead of
inventing a second untap rule. Every emitted node requires the shared current
layer-6 static-component fragment. Cost changes, free casts, dynamic quantities
or comparisons, linked objects, spell-self riders, Split second, Epic, grants,
copies, text changes, and unsupported compound siblings remain outside this
static grammar.

`compiler/static_player_restrictions.py` also lowers fixed acting-player turn,
opponent nonhand casting, nonartifact cast limits, and source-controller-turn
opponent restrictions. A coupled cast/activation sentence produces separate
event-bound nodes with the same printed source span and distinct role keys.
Both require the complete typed player restriction capability. Handler versions
and old payloads stay unchanged; the additive selector has its own closed
version. Actor turn, source controller, spell origin and current ability-source
zone remain separate facts. Nonartifact limits read completed cast events,
including earlier and countered spells, through the existing turn journal.

`compiler/temporary_cast_restrictions.py` separately lowers fixed resolution
instructions that prohibit all, creature, or noncreature spells through end of
turn for opponents, all players, or one targeted player. The immutable
`temporary_cast_rule_model.py` descriptor retains the resolving controller and
target seat in the existing duration journal. Shared proposed-spell legality
applies the rule to both advertised actions and accepted casts from every
otherwise permitted origin. Existing stack objects, land plays, and abilities
remain unaffected. Runtime binding independently validates the descriptor and
requires its capability; composite target schemas cannot turn an untargeted
restriction into a targeted one. Other durations, variable predicates,
latest-spell-color rules, and unowned sibling effects remain residuals.

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

## Paid entry-copy keywords

A printed fixed ordinary-mana Offspring instance emits an admitted additional
cost component and its linked entry trigger. The accepted cast option seals
the instance's paid fact through stack-to-battlefield entry. The trigger reuses
the canonical current/LKI source-copy recipe with a 1/1 exception. Unpaid casts,
blinked entries and newly created token copies cannot invent paid facts.
[ADR 0118](../adr/0118-paid-entry-copy-casting-facts.md) defines the cost,
occurrence and copied-casting-decision boundaries. Fixed ordinary-mana Squad
adds a separately validated integer payment count and one simultaneous source
copy batch. Its published count field is independent of printed X and has no
arbitrary repetition ceiling. See [ADR 0119](../adr/0119-fixed-mana-squad-repetitions.md).
Broader costs and multiple printed instances remain residual.

## Commander Learn

The complete Learn instruction lowers to the existing optional-payment
operation's closed schema-5 Learn action. Its capability is Commander-only;
runtime also verifies the effective Commander rules profile. The locked
controller may discard one current owned hand card, followed by one canonical
private draw, or decline. Commander rule 903.11 prevents the outside-the-game
Lesson option. Unknown profiles, broader subjects and unrepresented Learn
replacement effects remain unsupported. See
[ADR 0117](../adr/0117-commander-learn-payment-boundary.md) for the schema,
format authority, payment delegation and replay boundary.

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

Fixed targeting prohibitions lower explicit acting-kind, controller and source
quality predicates with source spans. Attached fixed stat changes remain
independently capability-bound in the same node. Selected-face characteristics
feed the shared target query for both offers and command validation; see
[target legality](target-legality.md) for current and last-known source facts.
Public-zone effect closure materializes iterable mechanics before sibling
shape checks, so both registration and whole-card binding retain the same
closed effect authority.

Entry-copy value productions reuse the shared immutable copy-exception codec.
Retained name/stat and replacement-type fields require explicit version three;
older descriptors retain their key sets. Added mana, Crew, Dethrone and Myriad
abilities compile through the existing inner ability/keyword owners and add
those exact dependencies to the parent. Unknown children keep the original
source residual. Public graveyard-copy selection is restricted to creature
cards; it does not open hidden zones, history predicates or mana-spent queries.

Conditional intrinsic entry-copy grammar admits only the closed declared-attack,
current-turn battlefield-to-graveyard arrival, and actual-mana-spent predicates.
Version four binds the predicate separately from source characteristics and
copiable exceptions. Printed cost, declared X and a prior physical card
incarnation cannot substitute for the corresponding authoritative fact.

Conditional Embalm entry-copy exceptions require version five and explicit
recipe creation origin. Copy-added Changeling compiles through the existing
keyword/CDA owner and declares that exact dependency. Unknown conditions,
unadmitted children and generic token-origin inference remain outside the
production. Prospective token objects participate in intrinsic entry-copy
preparation without becoming committed battlefield choices prematurely.
