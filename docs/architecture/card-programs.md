---
title: "CardProgram architecture"
status: "current"
authoritative_source: "quorune/card_programs and schemas/card-program-v2.schema.json"
verified: "2026-09-04"
audience: "compiler, rules, replay, and extension contributors"
maintenance: "hand-maintained"
---

# CardProgram architecture

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

A current capability-bound counted search supersedes a reviewed search at the
same key only when source hashes, event, costs, targets and result semantics
match. The adapter records the superseded key. Persisted reviewed programs
retain their recorded instructions and compatibility path.

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

## Trust and replay

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
  `Partner`, `Partner with`, `Choose a Background`, and `Doctor's companion`
  lines compile to distinct trusted `game.setup` capabilities. `Partner with`
  also compiles its separate self-entry targeted named search through the shared
  private library-search and normalized trigger owners. One setup owner checks
  the current canonical declaration plus the printed legendary, Background,
  exact Doctor, or reciprocal named-partner predicate before it creates any
  state. Broad keyword metadata and Oracle prose have no setup authority. Named
  Partner variants, granted or changed setup abilities, and stale or duplicate
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
