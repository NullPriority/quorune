---
title: "Typed Oracle IR"
status: "current"
authoritative_source: "Oracle compiler implementation, CardProgram schema, and pinned corpus reports"
verified: "2026-09-19"
audience: "compiler, rules, and CardProgram contributors"
maintenance: "hand-maintained"
concern: "oracle-ir"
---

# Typed Oracle IR

`oracle-ir-v680` accompanies the completed characteristic-query correctness
transition. Existing type/color settings and conditional grant grammar remain
unchanged. Runtime reads use the independent `characteristic_query_boundary_version`
record marker to include the completed requested layer while retaining source
eligibility and historical absence. This is a correctness/non-harvest transition;
whole-card support effects are measured through canonical generation. See
[ADR 0160](../adr/0160-versioned-completed-characteristic-queries.md).

`CharacteristicQuantitySpec` version four carries closed current-value scopes
for a source controller's nonnegative life total, current opponents' poison
counters, or an attached recipient's colors or mana value. It retains the
existing reduction fields and adds no GameState field. Quantity versions one
through three retain their encoded forms; the new scopes are reserved for
modifiers rather than characteristic-defining abilities.

Quantity version five adds card counts across explicitly owned exile and
graveyard collections, raw hand and library sizes, and maximum opponent hand
size. Current owner-zone lists and the active player roster define membership.
Tokens and copies are excluded; a card matching both instant and sorcery counts
once. Face-down exiled cards count only in an unqualified card count. Filtered
counts never inspect their characteristics. Hand and library sizes read only
public card-object metadata, without inspecting hidden characteristics or order.
Independent producer capabilities bind these quantities to their consumers.
Versions one through four retain their existing encodings and behavior.

Live public-set modifiers use the registered quantified anthem descriptor.
Its existing fixed-query condition identifies the affected creatures; its
modifier holds integer coefficients, the typed quantity, and an explicit
recipient-exclusion flag. Exclusion requires a validated subset relationship
between the affected subject and counted domain. Independent fixed keywords
on the same Oracle span remain independently executable components.

For represented Fuse split cards, the source-wide face finalizer retains both
physical faces and emits one typed complete-source method declaration. Normal
program lowering keeps each half's executable body and derives a separate
`spell:<left-face-id>:fuse` program. Its provenance contains the correlated half
programs and printed characteristic declaration; the closed shape validator
checks the ordered effects, target projection, and dependencies. A missing
derived program blocks complete CardProgram admission.

The cast action payload selects this method with `cast_method: "fuse"`. Fuse
does not use the alternative-cost option kind. A fused hand-count amount uses
scalar schema version 7 and an explicit canonical `target_group`; versions 1
through 6 keep their existing encodings.

Typed Oracle IR is the deterministic compiler representation between pinned
Oracle/rulings input and canonical CardProgram abilities. It classifies source
text without giving Oracle prose runtime authority. The compiler and
[CardProgram schema](../../schemas/card-program-v2.schema.json) define the exact
node shapes; generated [compiler status](../COMPILER_COVERAGE_STATUS.md) owns
current counts and residual inventories.

## Compiler result

Source-self counter result quantities use the existing scalar amount owner.
The closed version-two descriptor names one canonical counter kind. Current
resolution reads the original source object's counters; departure uses the
sealed last known snapshot, including before sacrifice activation costs.
A returning incarnation never substitutes. Missing snapshots fail closed,
while a known empty counter map yields zero. Version-one scalar descriptors
retain their original field set and serialization.

Fixed damage can bind a source pronoun after a closed source-self tap, untap,
entry, or departure event. The original source remains the damage source after
departure. Attached and other event participants keep their separate identities.
Vehicle self-attack heads retain the same source identity predicate as creature
self-attack heads; the printed type label does not become an observer of other
Vehicles attacking.
For a closed self-attack, fixed event-player damage can bind a source pronoun
while its recipient stays untargeted. Attacking a planeswalker uses that
permanent's controller as the defending player.

Fixed sacrifice prices can supply the mana value of the paid permanent to a
closed draw, life, counter placement, or single-color mana instruction. Scalar
schema version twelve reads the sealed battlefield incarnation and its
effective mana value from the committed cast. A spell copy carries the same
fact. Each instruction keeps its existing result owner, including target
revalidation and private draws.

A destruction instruction with a committed-result quantity retains its fixed
battlefield set and following instruction in one source-spanned node. The
result uses the type, token status, and controller recorded before movement.
It counts successful logical destructions after the destruction and destination
choices, including a destruction redirected to exile. The following draw,
life change, token creation, or source counter instruction receives that quantity
after the destruction commits and any required physical order is complete.

A targeted public graveyard exile can bind a follow-up to its committed card
incarnations. The scope uses the public front-face characteristics before the
move. A union counts one card once, and the resulting life, token, or source
counter instruction runs in the chosen ability or modal sequence.

A fixed multiplayer top-card reveal uses one actual card from each nonempty
library of a player still in the game. The reveal uses the front face of a
double-faced card. The resulting land and nonland counts drive the supported
instructions in printed order before the ordinary APNAP draws. A draw
replacement can change a later draw without changing the reveal results.
An activated ability that adds mana and instructs a draw uses the stack
under CR 605.1a.

Current-turn conditions read committed casts, declared attacks, discards,
permanent entries, graveyard arrivals and life changes from the existing
history journal. An intervening condition is checked at occurrence and
resolution, including a scheduled self-sacrifice. A casting discount or
static ability uses the same facts at its own evaluation boundary. A missing
journal remains unavailable even for a condition that requires zero events.

Committed public conditions can read a paid Warp cast and an actual
graveyard departure. Void uses a paid Warp cast or a nonland permanent
departure by any player. A graveyard condition uses the card owner's
departure fact, including when the card is cast. Token creation conditions
share the creation owner's per-turn count, which excludes a permanent
spell copy becoming a token on resolution.

Distinct-value amounts count the locked controller's current creature powers
or mana values in a closed public collection. Battlefield reads use effective
characteristics. Graveyard reads use the public card view, including combined
split-card mana values and the front face of a double-faced card.

Draw and token amounts for each card the controller discarded this turn or
each creature that died this turn use the typed scalar history reader. The
amount uses committed events when the instruction applies, including events
from earlier instructions of the same spell or ability.

Power thresholds read the current effective powers of creatures the controller
controls. Coven counts distinct signed powers and Formidable sums signed powers.
A trigger with an intervening condition checks at occurrence and resolution.
An activation uses the same aggregate when generating and validating legal actions.
An ordered conditional instruction reads the game after its preceding instruction applies.

Qualified battlefield counts read current effective power, toughness, or
represented modifications at the instruction that uses the count. A granted
draw may read its own independent count. In an ordered spell, a later declared
amount can read the hand after an earlier draw commits.
Single-instruction amounts written as equal to a public count retain the
existing public-query representation. Both damage word orders preserve their source and
target schema; the compiler proves that only the result amount varies.
Implicit scaled counts retain multipliers already represented by the public
quantity owner. Source-bound bodies use the same quantity validation after the
fixed-shape proof, and existing scalar producers keep their own coefficients.
Variable damage to each opponent uses the existing simultaneous damage batch.
A declared amount shared by damage and life gain is determined at resolution
and remains independent of how much damage prevention allows through.
Counted energy uses the controller's current battlefield quantity at the
instruction that grants it. A zero count creates no counter placement event.
Spells with one fixed creature sacrifice can read the power or toughness
sealed when that price is paid. The receipt follows the spell through copies
and replay. Result amounts apply the ordinary zero floor.

For each Oracle face and material ability, compilation records:

- Oracle ID, face identity, stable ability key, and active zones;
- exact normalized source span and source-text hash;
- typed costs, timing, modes, targets, choices, effects, triggers, static
  descriptors, replacements, prevention, and continuations where recognized;
- fine-grained capability and runtime-component requirements;
- applicable Comprehensive Rules and ruling provenance;
- compiler/template identity and deterministic semantic fingerprint; and
- every unmatched material span as a classified residual.

The CardProgram groups these abilities with card-level source hashes,
provenance, trust basis, closure, residuals, and one deterministic artifact
fingerprint. An omitted family means the artifact does not declare it; it is not
evidence of universal support.

## Stages

```text
pinned Oracle face and rulings
        |
normalization and source spans
        |
declaration and clause parsing
        |
typed IR nodes + material residuals
        |
CardProgram lowering and validation
        |
capability, interaction, and profile closure
```

Normalization selects faces and preserves written instruction order. Parsing
recognizes closed grammar and retains exact spans. Lowering produces versioned
typed constructs. Trust validation checks the complete materially reachable
program; it never treats a syntactic match as implemented behavior.

Closed standalone mana-symbol explanations, exact transform-back-face labels,
and Dryad Arbor's exact type-derived reminder are nonexecuting and do not
become abilities or material residuals; their authority comes from structured
mana costs, paired-face layouts, and printed card types. Intrinsic basic-land
mana and Class reminders retain their typed owners. Room, Siege, transforming-
Saga, meld, and other structural reminders remain source-spanned residuals
until their complete card-form owners exist. Mixed or unbalanced text also
fails closed, and the runtime never interprets reminder prose.

## Status meanings

- `exact`: every material span is represented by validated typed constructs for
  the compiler claim.
- `partial`: at least one material span lowers, while another remains residual
  or a runtime dependency is unresolved.
- `unresolved`: no safe executable representation exists for material text.
- `intentionally_ignored`: the span is reviewed as immaterial to the declared
  operation/profile and carries explicit provenance.

Compiler exactness, CardProgram construction, capability closure, supported
profile closure, and trusted execution are separate claims. A card can parse
exactly while remaining unavailable because a target, replacement, layer,
copy, multiplayer, privacy, or runtime dependency is not trusted.

## Provenance and identity

Compilation is deterministic for the same Oracle/rulings snapshot, compiler,
templates, capability registry, and policy. Source hashes and spans distinguish
changed wording from an implementation change. Stable ability identity must be
unique within its card; ambiguous keys fail closed instead of selecting a node
by iteration order.

The complete CardProgram and trust fingerprints are pinned in new Game Records.
Historical records deserialize their pinned artifact. A current compiler
correction changes the relevant compiler/template and artifact fingerprints;
it does not rewrite an old record.

## Runtime boundary

`CommanderSession` registers validated generated and reviewed abilities under
stable identities. A reviewed compatibility ability normally supersedes only
the same key. A trusted typed multi-event program instead replaces a complete,
body-identical reviewed split of its source-self subscriptions so one printed
ability cannot trigger twice; incomplete or ambiguous overlap remains
uncombined. Conflicting face or source identity fails loading. Exact executable
nodes enter registered typed semantic handlers or runtime components.
Unsupported or untrusted nodes remain unavailable in strict play or reach an
explicit development-only arbitration boundary.

No running game parses Oracle prose. The runtime consumes only validated,
fingerprinted CardPrograms, registered descriptors, and typed rules owners.
Fixed and public-value cast-cost text lowers to closed signed mana vectors and
public quantity descriptors. The descriptor names spell predicates, source or
caster relation, current origin/turn context, and only the represented public
metric; it never retains Oracle prose as runtime authority. Unsupported target-
relative, private, characteristic-cycle-sensitive, capped, or open-arithmetic
forms remain source-spanned residuals.
Closed public trigger bindings serialize only a normalized event identity and
typed predicates over sealed public facts. Current equipped relations,
last-known attached-object death, prior controller, owner-relative graveyard
departure, fixed entry characteristics, controller-another Cycling, and exact
one-or-more batching remain data in the CardProgram; the runtime never reparses
their Oracle wording. Controller attack thresholds, typed discard and
sacrifice subjects, fixed public zone predicates, and bounded aggregate attack,
damage, token-entry, and graveyard-departure subscriptions use the same event
and condition fields. Unsupported attachment, tap-state, first-time,
once-per-turn, ambiguous, linked, chosen, or dynamic event forms remain
residual.
An exact same-zone two-event clause serializes its ordered subscriptions inside
the typed event condition. CardProgram V2 projects both trigger entries while
retaining one semantic ability identity and one shared effect list. The runtime
matches only the selected committed event and never splits or reparses the
Oracle source line.
When an intervening condition applies to both events, each subscription carries
that condition alongside its own occurrence filter. Trigger discovery uses the
selected subscription. Resolution rechecks the shared condition for the
controller captured when the ability triggered. Saved programs keep their
original condition descriptors.
Versioned scheduled-player results retain the step participant selected at
occurrence. If that participant leaves before resolution, instructions addressed
to that player are skipped and instructions for surviving players continue in
their original order.
An intervening condition on the upkeep participant's hand count checks the
public count at occurrence and again at resolution. If the participant leaves,
the recheck uses the count immediately before departure.
Combat declaration costs, restrictions, and requirements are lowered as
closed static-ability fragments. Their registered handlers add the fragments
to the same effective layer-6 ability snapshot used by other static abilities;
the declaration solver filters that snapshot by typed fragment class and never
consults display text. Fixed public declaration conditions reuse the shared
public-state condition descriptor, while attached all-ability and nonmana
activation prohibitions remain typed current fragments rather than runtime
text. Source-controller restrictions retain their originating static source.

## Residual and override policy

A residual identifies the smallest unsupported material span, compiler stage,
reason, and expected dependency. Repeated wording gaps should become one generic
grammar production and reusable runtime family. Do not add a card name,
collector number, set code, or Oracle ID branch to the generic runtime.

A genuinely irreducible reviewed override is compiled data. It pins Oracle ID,
face/ability, Oracle and rulings hashes, residual classification, rules and
capabilities, authoring/review provenance, and deterministic assurance. See the
[override guide](../extension/card-override.md).

## Commands

```powershell
.\.venv\Scripts\python.exe simctl.py oracle parse "<card>" `
  --db data/scryfall-current.sqlite3
.\.venv\Scripts\python.exe simctl.py oracle explain "<card>" `
  --db data/scryfall-current.sqlite3
.\.venv\Scripts\python.exe simctl.py card compile "<card>" `
  --db data/scryfall-current.sqlite3
.\.venv\Scripts\python.exe simctl.py card audit "<card>" `
  --db data/scryfall-current.sqlite3
.\.venv\Scripts\python.exe simctl.py card coverage `
  --db data/scryfall-current.sqlite3
```

Use [compiler architecture](../architecture/compiler.md),
[CardProgram architecture](../architecture/card-programs.md), and
[rules assurance](../rules/assurance-model.md) when extending the pipeline.
