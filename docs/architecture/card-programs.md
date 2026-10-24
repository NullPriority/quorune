---
title: "CardProgram architecture"
status: "current"
authoritative_source: "quorune/card_programs and schemas/card-program-v2.schema.json"
verified: "2026-10-16"
audience: "compiler, rules, replay, and extension contributors"
maintenance: "hand-maintained"
---

# CardProgram architecture

Commander-qualified live grants retain physical designation in a typed object
query, separate from copiable characteristics. Their owner/control relation is
bound from the current grant-source controller. The outer grant and every inner
quoted program require independent capability closure, including the scoped
commander designation query. Ordinary copies receive no commander designation.

`CardProgram` is the canonical, deterministic artifact that connects pinned
Oracle input to runtime behavior. One artifact groups the executable abilities
for an Oracle ID and records face identity, source hashes, provenance, typed
nodes, material residuals, capability dependencies, trust basis, and a single
artifact fingerprint. The versioned JSON schema is the serialization authority.

## Ownership and flow

`quorune/card_programs/` owns validation, deterministic
serialization, adapters, inspection, and source-identity checks. It does not
own `GameState` and cannot mutate a game. The compiler produces typed nodes and
residuals; the registry combines generated and reviewed inputs; strict
preflight binds the resulting program to capability, handler, and component
fingerprints. Runtime code executes only registered typed operations. It never
parses Oracle prose during a state transition.

A reviewed ability may replace generated output only for the same stable
semantic key. Ambiguous ability identity, conflicting nonempty hashes, stale
source data, unknown dependencies, material residuals, or fingerprint drift
fail closed. Historical semantic-pack records remain compatibility inputs, not
a second current runtime authority.

Current fixed additional-token programs can supersede a source-identical
reviewed view through `additional_token_equivalence.py`. The generated program
must have trusted closure and the same complete handler, schemas, conditions,
and execution role, with matching Oracle and rulings hashes. Only after that
equivalence is established may a source-matched empty normal-permanent cast
view yield to the existing card-form owner. Nonempty, stale, conditional,
multiface, or provisional compatibility views remain intact.

`empty_permanent_equivalence.py` applies the empty-cast boundary independently
when the complete native source is exact and every generated ability is trusted
and bound to the current rules profile. It accepts only normal single-face,
nonland permanent records with current matching Oracle and rulings hashes.
The reviewed view must be an empty, nontargeted, unconditional stack-to-battlefield
cast with no extra cost. Nonempty reviewed abilities remain authoritative under
the existing precedence rules; partial source, stale data, unsupported faces,
and unbound generated dependencies cannot authorize this migration.

`trigger_view_equivalence.py` compares complete source-matched trigger views
with current trusted dependency closure. It retains event, role, result, cost,
limit, and target meaning. Closed scalar event conjunctions may reorder their
leaves; only `creature.dies` aliases its sealed previous controller to its
controller field. A single positional destroy target may use the existing flat
or one-group target codec, and its textual reason remains reporting metadata.
Other result/source differences, missing predicates, or ambiguous matches do
not establish equivalence. Existing private-draw defaults keep their current
normalization; historical payloads are not rewritten.

`activation_view_equivalence.py` handles same-key activated views only when the
existing pinned catalog contains the same complete ability and reproduces the
entire canonical price. The current result, targets, conditions, limits, role,
source hashes, and dependency closure must remain equal. An absent explicit
legacy cost schema never removes mana, sacrifice, energy, exile, tap, or other
catalog costs. Token creation comparisons may omit only a leading `Token`
display prefix when the existing type parser proves identical types; the
creation operation still produces a token with all its own characteristics.

Current typed modal activations can supersede catalog-only reviewed views only
through `modal_catalog_equivalence.py`. It requires matching source hashes,
ability identity and runtime role, exact catalog cost fields, one-mode
cardinality, and equivalent target/result bodies in source order. Historical
mode identifiers and serialized reviewed payloads stay intact; only current
assembly precedence changes. Typed modal schemas independently require every
branch owner, and an ordered branch retains its own instruction-list scope.
The comparison copies payloads before normalization and never mutates either
snapshot.

Compiler-declared complete-source boundaries can also belong to ordinary
effect programs. Their provenance explicitly requires the generated whole-card
admission certificate. Source validation rejects missing, malformed or partial
certificates before execution. This complements the existing handler-declared
complete-card boundary without changing historical programs that lack the new
provenance declaration.

Source-derived spell variants retain separate semantic identities within the
same CardProgram. A fixed Overload alternative binds the normal and derived
body fingerprints to its price. Both executable programs must be present and
current. The cast selects the derived program through its validated cost
option, preserving ordinary spell characteristics and an empty mode list.
Copies retain that selected program through the existing stack-copy owner.

Fixed Cleave uses the same source-correlated program boundary with its own
bracket-derived body, price and semantic identity. Each form retains its actual
target schema; this includes variants where both forms are targeted or both are
target-free. Neither paid-text choice creates a printed modal choice. Shared
validation retains the existing Overload keys and payload shape.

A current capability-bound counted search supersedes a reviewed search at the
same key only when source hashes, event, costs, targets and result semantics
match. The adapter records the superseded key. Persisted reviewed programs
retain their recorded instructions and compatibility path.

Reviewed optional-Kicker target overrides share one source-pinned equivalence
check with current typed replacement programs. It compares the complete normal
and paid target domains, result instructions, fixed price, event, version and
Oracle/rulings hashes. Only the equivalent current view supersedes the reviewed
key; historical payloads stay unchanged. A missing or different price, target
restriction, result or source hash retains the compatibility boundary.

Fixed counted library searches use a shared immutable schema consumed by the
compiler, capability shapes and private search owner. The compiler admits
unqualified quantities, closed characteristic qualities, one literal card name
as query data, and fixed mana-value comparisons. It preserves each instruction's
reveal policy and shuffle order. A stated hidden-zone quality allows failure to
find; an unqualified quantity requires as many cards as possible. Multiple hand
results move through the simultaneous zone owner and retain independent
commander replacement choices. A found top-card result remains in the library,
is excluded from the shuffle, and is placed on top without changing its logical
identity. The shuffle and placement expose no intermediate top-card view.

Color-qualified permanent searches constrain both color and permanent card
type. Subtype qualifiers use the shared pinned vocabulary; arbitrary adjectives
never become subtype names. The existing selector codec cannot represent type
exclusions. Nonland permanent searches therefore remain material residuals,
and the codec and search owner reject exclusion-bearing descriptors rather than
discarding their constraint.

Dynamic counts and predicates, name alternatives, split destinations, searches
of another player's library, and multiple top or graveyard results remain
outside this contract. Unsupported siblings retain their source spans and
prevent whole-card admission. Runtime code validates the typed schema and never
uses a card name to select rules behavior.

Stack resolution never uses display or Oracle prose to decide whether an
untrusted permanent program may resolve without its arbiter boundary. An
intrinsic Siege transformed-cast choice for a nonpermanent face likewise
requires either a typed target schema or a current trusted target-free spell
program before that choice is offered. Missing typed cast semantics fail closed.

Printed fixed-positive-mana Multikicker uses a source-pinned cost component
and an independent linked entry-counter component. The server prices the
selected repeat count before total-cost modifiers and seals the instance,
cost fingerprint and count; clients supply only the count. Zero payments use
the ordinary cast and carry no new fact. Complete-card admission gates the
paid branch, so an unsupported sibling cannot expose a payable partial card.
The typed fact in `multikicker_model.py` remains distinct from ordinary Kicker's
single-payment Boolean and from Offspring/Squad copy decisions. Spell copies
retain casting decisions; permanent copies and later noncast entries receive
no paid history. Unknown cost variants, instances and linked results remain
residual. This adds no runtime Oracle parser or second component registry.

Mandatory fixed mana per extra target uses a complete-source cast-cost
component rather than a repeat-payment casting choice. The typed descriptor
records its fixed mana vector and free-target threshold. Cardinality and price
dependencies are derived independently from the persisted target schema,
selected-count amount, and cost handler. Source admission requires both the
complete target result and mandatory price. The ordinary source hashes,
component registry, and final cost commitment retain their authority.

Persisted additional-cost schemas independently require the capability of each
selected zone-change leaf. A binary alternative also requires its choice owner
and every leaf owner, including branches that the player does not select.
The existing typed cost decoders validate these payloads before trust binding;
missing producer annotations cannot remove the price's authority requirement.

Optional life for colored mana reductions uses a registered cost component on
a complete current battlefield source. The offer seals the provider's object,
incarnation, program and fixed price. Equivalent providers use count choices
instead of enumerating source subsets. Ordinary cast acceptance recomputes
the choices, checks total life affordability and retains a locked discount if
another cost later removes its source. The life transaction remains the mutation
owner. Selecting a free base cost can still include the optional life payment.

Version-two counter target sets reuse the registered counter-placement handler
and canonical counter transaction. Their payload independently requires the
quantified-set capability; a named relative amount also requires named doubling.
The target schema retains scalar predicate dependencies and, for unrestricted
sets, cardinality authority. Original integer-only payloads retain their earlier
wire shape. Pending named placements seal the original target incarnations and
quantities, including targets with no named counters, before replacement choices.

All-kind doubling has its own typed operation and producer dependency. It
reuses the counter intent with an explicit all-kinds quantity mode and no
counter name. The existing source hashes and target cardinality still gate the
complete instruction. Pending replacement choices seal every selected object's
full counter map and logical identity; a newly added kind also invalidates the
choice. The existing generic intent and transaction retain atomic counter
mutation and replay ownership.

## Trust and replay

Unconditional static Aura control lowers into a closed attached-source
descriptor in the shared continuous-component registry. Runtime discovery
uses the current reciprocal attachment, source ability at layer two, and attachment
timestamp. The layer-two control owner composes this effect with existing
resolved control and retains initial custody for restoration. Changing the
Aura's controller changes its recipient-control effect. Source loss and a new
recipient incarnation end applicability. A later layer-six ability removal
does not undo the prior layer-two effect. Conditional and
enchanted-player scopes remain residuals.

A represented battlefield trigger may carry a closed optional `trigger_limit`
descriptor. The compiler composes the exact once-each-turn suffix with an
independently closed event and result, and retains the full source span.
Trigger discovery consumes the intrinsic ability's use when it triggers,
before stack placement. Countering it does not renew usage, and additional-
trigger effects cannot exceed the limit. Copying its stack ability retains
ordinary copy semantics. Current ability loss and control changes preserve
usage; a new logical object or global turn sequence renews the allowance.
Historical payloads without this descriptor retain their original shape.

Own-turn source casting grants use a registered complete-source descriptor and
the current static-ability applicability query. Each chosen price binds the
source incarnation, ability, current turn and underlying casting method. The
existing permanent-usage owner consumes that ability's allowance after a
successful cast. Ordinary casts and casts under independent permissions leave
it available; countering a granted spell does not refund it. Current ability
loss and control changes preserve the usage data. A new incarnation, a separate
copied permanent or a new turn has its own allowance. Canonical zone reset,
save/load and replay retain their existing ownership.

Flashback, Disturb and other methods supplying their own zone permission are
independent routes and never consume a separate source grant's quota. A
mana-only alternative can still combine with a source's ordinary graveyard
permission. A represented grant follow-up is sealed on the original spell by
the object-state owner and lowered into ordinary zone replacements. It does
not gain Flashback self-replacement priority, get copied to a spell copy, or
remain on a later incarnation. A tapped-entry follow-up participates before
entry rather than tapping the resulting permanent afterward.

Version-three source grants can require the current graveyard incarnation to
have arrived from its controller's library this turn or to have been actually
milled this turn. Those are distinct journal facts. A shared land-or-permanent
grant consumes the same source ability allowance for either action; a land play
also consumes the ordinary numeric land allowance. Independent graveyard land
permission preserves the source use. Explicit modern Mill-history recording
stores only committed graveyard results after replacements; absent historical
version fields remain absent and cannot invent prior qualifying cards.
A distinct nonbattlefield-arrival qualifier reads the same exact current-turn
incarnation journal while excluding battlefield-origin moves. It keeps its own
registered handler and capability requirement; a death cannot acquire this
permission from being in the graveyard alone.

Current closed fixed-color land-entry triggers supersede an exact matching
legacy output view and its empty permanent spell view only when Oracle and
rulings hashes, output amount, source identity and timing agree. The canonical
trigger owns its controller predicate and five-color domain. Historical
compatibility payloads remain available; unrelated or changed views remain
independent.

- Parsing success does not imply complete rules support.
- Trust cannot exceed the closure of targets, costs, zones, events,
  replacements, runtime operations, and the selected rules profile.
- Unregistered or provisional behavior remains explicit; registration never
  promotes a program by itself.
- Game records pin the program and applicable registry fingerprints. Replay
  validates those values when present and uses the recorded artifact rather
  than silently recompiling it with a newer compiler.
- Program descriptors may participate in later events through runtime
  components, but those components receive bounded immutable contexts and do
  not mutate state.

Paired Daybound/Nightbound programs and legacy previous-turn self-transform
programs carry the shared current-ability fragment marker. Runtime face and
trigger discovery therefore consult the same layer-6 component applicability
query used by other static components; removing an ability suppresses future
participation without erasing a trigger already on the stack. The trigger
captures both source logical identity and transform count, while the public
day/night designation and bounded previous-turn summary remain Game Record
state rather than CardProgram data.

Combat declaration grammar is compiler-only. Exact costs, restrictions, and
if-able requirements lower to registered static-ability fragments. Closed
two-fragment source conjunctions retain both handlers, while exact public-query
and attachment characteristics may grant those same fragments in layer 6.
Declaration queries consume the one current effective layer-6 fragment
snapshot. This keeps copied, removed, and granted abilities on the shared
characteristic boundary and gives raw Oracle or token display text no
declaration authority.

Nonkeyword attack triggers participate only through typed triggered-ability
fragments. Their event is dispatched from the sealed canonical attack
transition, batched through the ordinary trigger subsystem, and resolved by
their trusted semantic program. Token reminder or Oracle text is never used
to discover the trigger.

Quoted activated, fixed-output mana, and triggered abilities use one shared
compiler boundary for fixed attachment and live-query grants. A closed public
condition can gate the same typed fragment, optionally beside fixed keywords
and a power/toughness modifier. Its schema-v2 handler keeps the schema-v1
characteristic descriptor unchanged. Current layer-6 availability controls new
offers and trigger discovery; an ability already on the stack retains its
compiled program after the grant ends. Fixed source modifiers use the existing
logical-object owner, and grant carrier metadata does not bypass the inner
effect's ordinary shape and capability checks.

Explicit abilities in supported fixed creature-token definitions use the same
boundary. The compiler lowers the token shell and each independently exact
quoted child together, stores only typed copiable fragments on the created
token, and pins activated or triggered child programs to stable semantic
identities. The token remains the runtime source after the creating object
leaves. More than two quoted abilities, unsupported children, reminder-only
profiles, and nonfixed token shells remain residual.

Commander pairing is also a CardProgram declaration boundary. Exact ordinary
  `Partner`, the four pinned `Partner—[text]` variants, `Partner with`,
  `Choose a Background`, and `Doctor's companion`
  lines compile to distinct trusted `game.setup` capabilities. `Partner with`
  also compiles its separate self-entry targeted named search through the shared
  private library-search and normalized trigger owners. One setup owner checks
  the current canonical declaration plus the printed legendary, Background,
  exact Doctor, or reciprocal named-partner predicate before it creates any
  state. Tagged declarations share one capability and retain their exact closed
  ability variant; only identical tags pair. Mixed tags and other pairing kinds
  do not combine. Broad keyword metadata and Oracle prose have no setup authority.
  Unknown Partner variants, granted or changed setup abilities, and stale or duplicate
  declarations fail closed until their distinct typed owners are implemented.

## Inspection and extension

Use `simctl.py card compile`, `explain`, `audit`, `diff`, `trust-closure`, and
`runtime-components` against a pinned local card database. Inspection output
is derived from the canonical artifact; it is not an additional source of
truth.

Add reusable grammar and typed nodes before considering a card-specific
override. Preserve source spans, add positive and negative compiler fixtures,
declare exact capability dependencies, and regenerate owned reports. New
schema versions or changed architectural ownership require an ADR.

See [ADR 0005](../adr/0005-card-program-v2.md),
[ADR 0008](../adr/0008-runtime-trust-and-governance-hardening.md), the
[Oracle IR reference](../reference/oracle-ir.md), the
[compiler architecture](compiler.md), the
[typed-handler boundary](semantic-handlers.md), the
[runtime-component boundary](runtime-components.md), and the
[trust-closure model](trust-closure.md).
