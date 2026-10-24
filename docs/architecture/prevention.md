---
title: "Damage prevention"
status: "current"
authoritative_source: "quorune/damage_prevention.py, quorune/damage_prevention_creation.py, quorune/damage_prevention_aftermath.py, and quorune/semantic_runtime/damage_replacements.py"
verified: "2026-08-06"
audience: "rules, semantics, replay, and architecture contributors"
maintenance: "hand-maintained"
---

# Damage prevention

Prevention is a stage of the canonical [damage transaction](damage.md), with a
separate owner for durable modifier state. Static effects participate through
immutable runtime components. Finite and next-instance shields, chosen-source
effects, redirections, and represented aftermath use typed creation,
application, and result transactions.

## Modifier lifecycle

`damage_prevention_creation.py` validates a typed request, performs any
source choice, and creates a source-stamped modifier through the focused
mutation owner. Public source candidates may be projected to the chooser;
private state and opaque object references remain authoritative only.
Physical and logical identity rules determine whether a later object is still
the chosen source.

During damage preparation, `damage_prevention.py` discovers applicable
modifiers, applies affected-player ordering, allocates bounded amounts, and
produces a mutation-only commit plan. Unpreventable damage does not consume a
shield. Finite modifiers retain their exact remainder across events; expired
or exhausted modifiers are removed by their declared lifecycle owner.

Each durable shield carries closed damage and recipient scope. The represented
scope distinguishes any damage from combat damage and all recipients from
players only. The replacement query composes that scope with the
existing subject and incarnation-pinned chosen-source predicates, so excluded
noncombat or recipient-kind events neither apply nor consume the shield.

Fixed all-damage instructions can additionally carry one closed public source
scope and one closed public recipient scope. The same immutable scope value is
used by until-end-of-turn shields and battlefield-static components. It covers
current controller or opponent relations, source or attached-object identity,
one already selected direct target, fixed type, subtype, color, and keyword
predicates, and any, combat, or noncombat damage. Sealed snapshots supply
attacking, unblocked, token, enchanted and
no-plus-one-counter facts where the printed grammar calls for them. Complete
current block-pair identities support prevention between opposing combatants;
recipient color and shared source-recipient color remain explicit predicates.
Redirection replaces recipient colors, supertypes, combat partners and scope
facts together, so prevention follows the new recipient. Static discovery requires
the current layer-6 ability component, recomputes controller and attachment facts,
and stops when the source leaves the battlefield. Turn-bound shields freeze
their resolved public identities and expire through the ordinary cleanup
owner. Both forms still enter the same affected-player replacement ordering.
Negative type, subtype, keyword, and color predicates distinguish an explicitly
known empty collection from unavailable current or last-known facts. Missing
facts fail closed instead of satisfying a `contains none` predicate. When two
sibling scopes lowered from one `dealt to and dealt by` instruction both match
the same damage event, their explicit semantic application-group identity
collapses them to one ordering option and one public application. Independently
created shields remain distinct even when they share a source and label, so the
selected shield alone owns its aftermath or triggered consequence. Each sibling
scope remains independently available for later nonoverlapping events.

Finite recipient-set prevention uses a closed version-one selector to capture
current creatures and active players when its instruction resolves. It creates
one ordinary fixed-amount shield for each recipient, including creature tokens.
Phased-out objects and noncreatures receive no shield. Later entrants, later
animation and new object incarnations cannot inherit those shields. The
selector has an independent producer capability; existing selectors and shield
records keep their encoded forms and canonical lifetime and consumption owners.

Resolved prevention can bind an enchanted, equipped or fortified creature
through the existing attachment-reference owner. The compiler proves the source
relation and emits a versioned bound-subject marker with a version-two
reference. Activation captures the relation before costs; departure refreshes
pending source information before zone reset. Missing recipients produce no
shield and no source-choice prompt, rather than an implicit controller shield.
Created shields pin their resolved recipient independently of later attachment
changes. Older unmarked prevention instructions retain their existing defaults.

Plain retained entry colors and creature types can parameterize a version-one
prevention predicate. Server capture binds the value to the activating source
incarnation before costs, so reentry with a new choice cannot alter an old
activation. Color Circles choose one eligible damage source; the chosen-type
scope instead creates one next-instance shield for any matching creature.
An absent choice resolves to no effect, never an unqualified predicate. Literal
predicates keep their existing encoding and damage-time property checks.

Fixed targeted-source prevention lowers directly to a canonical chosen-source
identity without asking another source choice. Targeted recipient shields pin
the resolved recipient through the creation owner, rather than retaining only
a public reference in an applicability filter. Source and recipient reentry
therefore cannot inherit a prior incarnation's shield. Shared direct targets
compose tapped creature state with a closed subtype disjunction and revalidate
both restrictions on resolution. Literal source keyword requirements retain
their ordinary choice-time validation and damage-time property recheck.

Preparation and commit are separated. A continuation fingerprints the damage
event, available amount, source snapshot, modifier set, chooser, and prior
journal. If any input drifts before resume, validation fails without partial
mutation.

## Aftermath and ordering

Wording dependent on damage "prevented this way" uses the prevented-amount
aftermath transaction. Fixed static self-prevention has a separate closed
additional-result component: it removes one +1/+1 counter for the whole
simultaneous damage occurrence, regardless of the number of sources. Its
unconditional form still prevents at zero counters; the represented
while-has-counter form requires a current counter. Unpreventable damage is
dealt and still produces this independent removal, while finite shields retain
their remainder. The existing partial counter-removal owner does as much as
possible; this result is not a removal cost.

Additional counter results retain their applying source incarnation through
redirection and use current layer-6 component presence. Nested damage prepares
against detached counter overlays, including earlier nested removals, and
validates the complete removal tree before any live damage commits. Copying,
face-down entry, protection ordering, private suspension, and replay retain the
same canonical boundaries. Independent later instructions remain ordinary
ordered siblings. Typed aftermath can route represented life, counter, or nested
damage results through those subsystems' canonical replacement-aware
transactions. Triggered prevention aftermath requiring a stack object remains
a trigger concern, not an implicit immediate effect.

Competing prevention effects follow the affected-player ordering rules and are
rediscovered after each application. Simultaneous events preserve APNAP and
same-chooser order in replay. Mana-payment and semantic-choice continuations
resume the exact original action after the prevention decision.

## Boundary

Runtime components and handlers never mutate shield state directly. Generic
code does not recognize card names or Oracle IDs. New prevention grammar must
compile to typed descriptors with exact predicates, provenance, duration, and
capability dependencies; unsupported predicates and aftermath forms stay
material residuals. Fixed self-prevention in both combat-damage directions
pins its source with the same canonical public source predicate accepted by
the shield creation owner; it does not issue a source-selection choice.
Unrepresented counter and combat-role predicates, dynamic power or
toughness, mana-value, secret, chosen, history-relative, linked-result, modal,
optional-prevention, repeated-decision, and unsupported multiple-target
predicates remain outside the represented fixed all-damage boundary. So do
combat-source exceptions and "all but N" quantities.

See [ADR 0012](../adr/0012-damage-transaction-and-static-prevention.md),
[ADR 0015](../adr/0015-durable-damage-modifier-ownership.md), and
[ADR 0017](../adr/0017-prevention-continuations-and-aftermath.md).
