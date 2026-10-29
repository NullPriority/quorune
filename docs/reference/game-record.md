---
title: "Game Record v3"
status: "current"
authoritative_source: "quorune/record.py, quorune/replacement/model.py, quorune/replacement/ordering.py, persistence implementation, and Game Record schemas"
verified: "2026-08-06"
audience: "engine, persistence, replay, and analyst contributors"
maintenance: "hand-maintained"
concern: "game-record"
---

# Game Record v3

Cipher records retain a version-one typed `cipher_encoding` link on the
physical exiled card, including both original incarnations. A committed encoded
object departure ends the link for future grants. Existing pending triggers
retain their own encoded-card reference and damage-time controller. Version-two
granted-trigger fragments add `cipher_card_ref`; version-one fragment fields
remain unchanged. Encoding and one-shot copy-cast continuations validate actor,
offered object identity and current casting options. Runtime trigger derivation
does not mutate the semantic registry. See
[ADR 0135](../adr/0135-cipher-linked-grants-and-copy-casting.md).

Game Record v3 is the authoritative persistence, replay, and audit directory for
one game. It separates canonical state and accepted inputs from delivery state
and derived review. The schemas under `schemas/game-record-v3-*.schema.json`
and the serializer implementation define the exact format.

New checkpoints carry `cast_source_placement_version: 1`, mirrored in manifest
format provenance. The marker places the actual casting source on the stack
before payment and completes that same spell with the paid facts. Historical
absence retains the earlier transition order and remains absent on serialization.
Manifest/checkpoint disagreement and unknown or boolean versions are rejected.
See [ADR 0130](../adr/0130-casting-source-placement-before-costs.md).

New checkpoints also carry `explore_action_version: 1` in matching format
provenance. Empty-library Explore attempts a counter on the retained current
explorer before completion through the ordinary replacement owner. Historical
absence keeps the former empty result and serializes without the marker.
Manifest/checkpoint disagreement and unsupported versions reject before replay.
Existing Explore instructions and Map builtins retain their schemas. See
[ADR 0134](../adr/0134-versioned-explore-action-semantics.md).

## Directory contract

| File | Contract |
| --- | --- |
| `manifest.json` | Public identity, lifecycle, profiles, source fingerprints, outcome, replay, and fidelity summary |
| `initial-checkpoint.json.gz` | Private exact replay origin, including physical objects and the pending decision |
| `checkpoint.json` | Current authoritative state without event history or raw capabilities |
| `commands.jsonl` | Accepted external commands with normalized payloads, principals, capability digests, RNG facts, and before/after hashes |
| `events.jsonl` | Normalized trace at the selected trace level; excluded from the authoritative state hash |
| `decisions.jsonl` | Accepted and rejected external attempts, legal alternatives, audit metadata, retries, and fallback status |
| `opportunities.jsonl` | Engine priority/opportunity delivery and suppression audit |
| `semantics.json` | Pinned canonical CardPrograms and compatibility semantic-key index |
| `cursors.json` | Delivery cursor state; not authoritative replay input |
| `pilot-profiles.json` | Optional advisory profile fingerprints by principal |
| `pilot-seat-memory/` | Optional bounded memory isolated by seat |
| `plans.json` | Remaining validated ordered actions for safe fixed-seat resume |
| `review.json`, `review.md` | Derived diagnostics and fidelity review; rebuildable and nonauthoritative |
| `hidden-information-audit.json` | Derived projection and reference-leak audit |
| `call-benchmark.json` | Derived observed provider/opportunity metrics with provenance |

Raw capability tokens, guest tokens, invite codes, provider credentials, and
network connection cursors are never authoritative durable state. A loaded
checkpoint reissues opaque capabilities from persisted digests and the current
pending decision.

## Canonicalization and hashes

Activated-ability payloads omit `boast` unless it is true. A current Boast
descriptor retains its typed original-source attack condition and existing
once-per-turn limit. Attack facts remain in the current-turn journal; usage
remains on the source incarnation through the existing activation owner.
Pending load preserves both facts. Earlier ability payloads retain their key
sets and constructor order when the optional marker is absent.

`CombatState.blocking_requirements` is an optional version-one array of resolved
block-specific-attacker requirements. Each entry retains a unique requirement
identity and both physical/logical combatant incarnations. Pending load keeps
the current combat relation; ordinary combat reset expires it. Empty books are
omitted so historical combat payloads retain their original bytes. Malformed or
duplicate requirement identities reject during decoding.

Authoritative JSON uses deterministic serialization and strict schema
validation. State hashes cover canonical authoritative state, including
versioned rules-relevant fields and continuations. They exclude presentation
text, raw capability tokens, connection cursors, and derived review.

Checkpoint serialization omits event logs and capability credentials before
constructing their payloads, while full `GameState.to_dict()` retains them for
its ordinary callers. The checkpoint remains a deep-isolated copy of every
retained authoritative field, with the same canonical payload and hash.

Every accepted command records:

- sequential record command identity and optional network idempotency identity;
- authenticated principal, decision, action, and normalized delegated choices;
- source/card program and runtime binding provenance when applicable;
- deterministic RNG consumption and results;
- before-state and after-state hashes; and
- the replay-relevant semantic/capability fingerprints used by the transition.

The record-trust owner loads the current capability registry when an action actually
resolves a semantic program and needs its binding-provenance row. Actions with
no such resolution retain their existing command and decision records without
constructing an unused binding registry. A later semantic resolution validates
fresh registry and evidence inputs; no action-level snapshot is retained.

An `opening.hand` decision records a versioned private continuation containing
turn order, remaining final-hand source incarnations and the current chooser.
Only that chooser's eligible references enter the projected payload. Nested
entry replacement choices use `opening_hand_entry` in the ordinary replacement
continuation; nonspell Aura choices retain the same opening frame. Resumption
revalidates the pre-turn window and original hand objects before entry. Older
records keep their recorded decision shapes and do not insert opening actions
after an already recorded game start.

Battlefield-only opening frames retain version one and its original bytes.
Version two names battlefield and reveal actions separately, pins their
semantic program identities, and records revealed hand incarnations with any
earlier disclosure. The final opening action ends that reveal duration before
starting the first turn. Pending records retain already created delayed
triggers; loading does not reveal again or recreate the scheduled result.

A spell with a paid hand-reveal cost records a version-one
`casting_hand_reveals` context containing selected physical/logical identities,
its stack reference and earlier disclosure. Pending load retains the reveal
until that original spell leaves the stack. Another spell revealing the same
card keeps its own duration; copied stack contexts cannot extend or clear the
original disclosure. Ordinary stack records omit this context and keep their
existing bytes.

Targeted ability frames may contain a version-one
`targeting_source_characteristics` context. Its closed fields identify the
physical object, public reference, logical incarnation, expected zone,
controller, type line and colors. Canonical departure capture updates that
binding before the source changes zones. Resolution uses current information
for the same incarnation or its captured departure information. Stack copies
of abilities retain the source binding while keeping their own controller.
The binding is internal; it does not add projected choice authority. Older
frames without it remain structurally readable under the existing execution
provenance contract.

Count-sensitive casting options use version three of the targeted-cost fact.
It retains the positive generic total, generic reduction total and each applied
source's identity, controller, component identity and signed adjustment. The
amounts are locked from the validated selected targets before payment. Versions
one and two retain their exact historical fields and serialization. Public
price rows for count-sensitive choices include signed generic adjustments so
the projected form can distinguish different counts from the same sources.

Self-cost metric schema five stores one closed `public_state` condition in the
existing term list. Earlier metrics omit it and preserve their payloads. The
prospective stack/controller view used to price it is read-only and ephemeral;
it adds no saved GameState field. Independent paired discounts retain separate
terms. See [ADR 0127](../adr/0127-prospective-public-self-cost-conditions.md).

Fixed additional tap prices record `fixed_additional_tap_payment` in the
selected casting option. Its closed version-one frame contains the compiled
price and each selected physical object, logical incarnation and reference.
The ordinary tap-cost reference list also contains any Convoke or Improvise
payment; the fixed reservation distinguishes those resources when validating
Delve and mana remainders. Save/load revalidates the original reservation rather
than replacing it with a new selection. Existing casting options omit this
frame and retain their serialized form.

Fixed random spell prices use the closed `random_discard` additional-cost
descriptor with schema one and count one. Suspended payments reuse the private
`_random_discard_cost_refs` response field and server payment identity. The
continuation accepts one distinct current payment reference, excluding the
casting spell, for a hand-to-graveyard discard event. It still validates the
affected object, event identity and original source cost before resuming. The
client cannot submit this internal field or choose the random card. Ordinary
selected costs and older continuations retain their payloads.

When a semantic choice commits an earlier intent before a later zone intent
needs replacement ordering, its continuation may carry `completed_choice`. This
closed payload stores only the remaining validated intents and their typed
consequences, bound to the original parent, payer, response and suspended
intent. Save/load resumes that unpaid work without checking or charging the
already-committed resource again. Continuations without this field retain
their existing decoding and validation path.

Random own-graveyard return instructions serialize a version-one closed subject
descriptor and fixed or declared-X count. They use the existing seed, stack
reference and instruction-pointer identity with a separate random-return
namespace. The simultaneous replacement continuation keeps the original
public candidate event until choices finish; it adds no mutable RNG stream or
optimization cache. Historical random-discard identities and selected-order
behavior remain unchanged.

Declared-X hand discard reuses the version-three `zone_change` cost descriptor:
`operation` is `discard_one`, `choice_field` is `discard_cards`, and
`count_parameter` is `x`. Its closed predicate selects unrestricted, creature or
land cards in the caster's hand. Graveyard-exile and fixed-count payloads keep
their original keys and meaning. The current proposal, stack X and dependent
private selector share one declaration; pending payments retain the original
selected refs and destination journal without adding a Game Record field.
The same stack X may originate from one represented additional price on a
fixed-mana spell. Copies retain it without paying again. The historical
`target.cardinality.mana_announced_x` capability identifier remains stable;
its existing count descriptor carries the same `x` parameter for these price
sources. No alternate target binder or additional persisted X field is added.

Fixed Waterbend activation descriptors omit `waterbend` unless present. A
`waterbend_payment` records its closed cost and selected physical objects,
public references and logical incarnations in the proposal or casting option.
The server's internal `_waterbend_payment` response field carries that exact
reservation through a rolled-back mana replacement continuation. Pilots cannot
submit the field. Resume rejects a changed selected incarnation, and ordinary
actions preserve their previous descriptor and continuation keys.

Preparation records use optional `prepared_designation` and `prepared_spell_copy`
annotations with the same closed version-one binding. It contains the source's
physical reference, logical incarnation, inset spell characteristics and linked
copy identity. The copiable `_prepare_spell` value stores alternative spell
characteristics separately from the noncopiable designation. Copies cast from
exile retain their link through stack creation so the accepted cast consumes
the original designation once. Ordinary records omit these annotations.

Object-selecting replacement journals may carry an optional `objects` array of
distinct selected references. The selected typed operation validates that set
against its captured candidates and shared entry reservations. Object choices
and prevention allocations are separate inputs; historical selections omit
`objects` and retain their encoded form. Prepared entry serialization may also
retain linked sacrifice zone plans in a nonempty `devour_sacrifices` array.
Each plan must match a selected physical/logical incarnation and controller.
Resume validates selected incarnations and current control before rebuilding
the move. Shared Devour resources use the existing simultaneous-event selector;
the payload associates each offered effect with its event and eligible objects.
Empty arrays are omitted so ordinary prepared entries keep their prior keys.

Derived mana activations omit `tap_source` for the historical tap-cost form,
which defaults to true. A nontapping self-sacrifice mode explicitly records
`tap_source: false`; its compact mode similarly includes `tap: false`.
Acceptance revalidates the flag against the trusted current ability, so a
supplied flag cannot bypass a tap cost. Ordinary tap modes preserve their
existing serialized shape.

Intrinsic entry-copy records preserve physical card identity separately from
copied program provenance. A copied triggered or activated ability records its
registered program identity; the resolving spell's generic fallback records
the originally announced physical spell. A pending copied-Aura attachment
choice retains the chosen replacement result and resumes without refreshing
the copy from its source.
Acquired entry-copy effects may serialize an optional `occurrence_id`, restricted
to entering-copy zone replacements. Alternative candidate effect IDs share this
identity; the applied-effect journal consumes the occurrence on acceptance or
decline. Historical effects omit the field and retain their serialized shape.
The internal `entry_copy_form` event value selects the immutable captured form
and gates its acquired copy abilities and other entry producers. Pending records
retain these values through save/load without exposing them in pilot packets.

Replacement effects with a mandatory alternative result may serialize an
optional `decline_label` for that second branch. It requires the existing
optional-replacement and alternative-operation fields, changes only the
projected label, and survives pending save/load. Historical effects omit this
field and retain their serialized values and default decline wording.

Resolution-created continuous effects may serialize `denied_keyword_grants`
as closed physical-object, logical-incarnation, and keyword triples. Each
triple must name a locked target and an ability-add operation in that effect.
These exclusions preserve a prohibited grant's absence across source departure
and save/load. Historical effects omit the field and retain their exact form;
current static prohibitions are recomputed from represented layer-6 sources.

The manifest pins engine, card-data, rules, compiler, CardProgram, capability,
semantic-handler, runtime-component, profile, and record-format identity as
applicable. Loading rejects internal disagreement before replay begins.

## Replay contract

Native records use `command_replay`. Verification loads the exact initial
checkpoint and pinned semantic identity, then resubmits each accepted command
through the ordinary permission and rules boundary. It verifies the before hash,
normalized transition, RNG facts, after hash, and final authoritative state.
The first mismatch fails closed.

Rejected attempts remain in `decisions.jsonl`; they never become replay
commands. An unfinished or paused record verifies its accepted-command prefix.
A passing prefix proves deterministic reproduction of that prefix, not a
terminal game or strategic evidence.

Historical records retain their recorded compatibility semantics. New optional
fields use explicit versioned shapes, and absence preserves the older hash and
meaning where the schema allows it. A current runtime never silently recompiles
or reinterprets an old record as if it used current Oracle, rules, or
CardPrograms.

Current games pin `qualified_attack_facts_version: 1`. Declared-attack
occurrences carry versioned public attacker characteristics sealed after
declaration and before discovery. Pending triggers retain those facts across
later characteristic changes, source departure, save/load and replay.
Checkpoints without the execution marker retain the older event contexts and
hashes; replay does not append the new fields. Unknown versions and Boolean
versions are rejected.
The same execution marker enables a sealed `declared_attacker_snapshots`
collection containing public traits, attacker logical identities and declared
recipients. Qualified batch conditions count this collection rather than live
permanents. Their aggregation key records the actual recipient kind and
reference and permanent incarnation when grouping by player or planeswalker, independently from the
defending controller. Legacy unmarked checkpoints omit these additive facts.
Receiver-bound attack characteristic results reuse the sealed
`event_counter_recipient` reference and logical identity. A departed or new
incarnation is skipped at resolution; the observer's identity remains the
effect's source. Public quantity evaluation retains the trigger controller.

Current games pin `spell_mana_counts_version: 1` in the initial checkpoint.
Each original cast stores an optional version-one `mana_spent_counts` stack
fact with its physical/logical incarnation and exact concrete WUBRGC amounts.
It is separate from casting decisions and the distinct-color set; spell copies
omit it. A checkpoint without the execution marker retains historical
no-count casting, so later replay does not add payment fields to its hashes.
The decoder rejects malformed versions, Boolean counts and copy-owned facts.

Current games also pin `ability_source_information_version: 1`. Every nonmana
activation captures its original source before costs, and every triggered
ability captures its source when created, including abilities without targets.
The existing targeting-source binding follows current characteristics while
that incarnation remains in its expected public zone and pins immediate
departure information through the canonical zone owner. An absent marker
retains historical targeted-only capture and does not add fields during replay.

`cast_face_offers_version: 1` enables Adventure alternative-face offers and
their intrinsic resolution lifecycle. A resolving Adventure records the chosen
alternative and normal face, moves to exile, and grants its resolving controller
a normal-face play permission bound to that exact exile incarnation. Countered
spells and spell copies gain no permission. Records without the marker retain
their earlier face offers and resolution. Both optional markers accept only
integer one or absence; Boolean and unknown versions are rejected.

Current runtime-trust provenance also identifies a core permanent-spell copy's
empty resolution through its pinned spell record. It retains the copy-specific
fallback kind and original Oracle identity, independently of whether the
original spell is still on the stack. Records without runtime-trust metadata
retain their earlier fallback-lookup behavior. This does not admit an
unsupported copy-producing card or alter copied casting decisions.

An event-bound spell-copy trigger stores an optional `event_spell_copy_source`
version-one context containing the original stack item, logical incarnation,
copiable characteristics, target schema and departure flag. Live references
read current spell choices; departure updates both placed and waiting trigger
occurrences before removal. The copy choice continuation retains the same
reference so load and replay cannot substitute a returned card. Execution
progress is excluded from the copied snapshot. Existing targeted-copy records
omit this context and retain their earlier behavior.

Paid-mana result instructions retain a closed threshold and an original-spell
mana-unit reference. The bound count and selected branch use the existing
resolution continuation; private Scry still uses its ordinary principal-scoped
selection frame. An optional `target_controller_information_lki` context book
stores version-one physical identity, zone-change counter and departure
controller only for a represented target-information reference. Loading keeps
that original incarnation; re-entry does not replace it. Earlier records omit
the book, and earlier resolution-condition schemas keep their payloads.

Scalar amount schema eight represents a committed cast spell's mana value.
Its origin and `mana_value` characteristic are closed, and it reads the
original normalized cast context rather than a current zone object or casting
payment. A declared scalar cache also binds that cast's incarnation, stack
reference, caster and value. The reader requires existing complete cast-event
facts and adds no checkpoint or event fields. Scalar schemas one through seven
retain their exact payloads.

Activation-condition schema two contains a closed `fixed_public_state` kind
and its existing typed condition payload. Source names participate only in
compilation; replay uses the pinned predicate rather than reparsing Oracle.
Legacy activation condition fields and fixed-mana payloads keep their original
forms. The new discriminator adds no game-state or event-history fields.

Public-condition schema eight stores one closed `history_query` and a
nonnegative count or amount threshold. Its version-one query identifies the
typed event, principal relation, card-type filters and reduction. It reads the
existing current-turn history; it adds no presentation-log dependency or new
event fields. Departure and graveyard-arrival queries require their existing
history-mode markers. An absent mode is unavailable rather than a known zero.
Condition schemas one through seven retain their original serialized shapes.

History-query schema two adds a closed origin selector and permits card-type
filters on graveyard arrivals. Complete facts require additive
`turn_history.spell_origin_history_version` and
`turn_history.graveyard_type_history_version` markers, each one when enabled.
New games enable them; rollover and reset preserve them. The record manifest
binds their versions to the initial checkpoint. Historical absence omits cast
origins and retains empty arrival types, so replay adds no previously absent
facts. Missing modes cannot establish a qualified zero-history condition.

`turn_history.spell_mana_history_version` enables complete current-turn spell
payment facts when set to one. Each `spell_mana_spent` fact stores its player
and positive actual paid amount. Reset clears the facts while preserving the
mode; historical absence omits it and adds no new facts during replay. The
manifest binds this mode to the initial checkpoint. Expend occurrences retain
an immutable player and before/after payment totals; later spending cannot
change a queued crossing.

The version-one `qualified_spell_cast_number` event condition stores one
closed spell-history query and a fixed positive equality threshold. It locates
the exact committed cast incarnation in the existing current-turn journal and
uses only the matching prefix through that occurrence. Later casts therefore
preserve the queued event's ordinal during replay and any intervening-if
recheck. It adds no fields to historical cast events or checkpoints. Earlier
unqualified spell-number conditions keep their serialized form.

Fixed event-player damage reuses the existing damage instruction and sealed
context player references. Its distinct capability identifies the closed
source/recipient binding; it adds no checkpoint fields or projected target
choice. Pending frames retain their original event player after source
departure and through save/load.

Upkeep-relative control history is one such additive boundary. New records pin
its version in both the initial checkpoint and manifest; records without the
marker remain in the historical no-history mode so later replay does not add
control-acquisition or upkeep timestamps that were absent from their command
hashes.

Custody-history version two also binds resolution-created control to the
continuous-effect journal. Initial custody is retained separately from card
ownership. Source-bound grants carry the original source incarnation and a
continuity snapshot; represented pending abilities retain their earlier
snapshot and resolution-start timestamp. `source_continuity` is omitted from
card payloads until needed. Version one and absent-marker states retain their
earlier control path; the new fixed-control capability requires version two.

The optional `soulbond_pairs` array retains version-one pair identities,
both exact object incarnations, their original controller, and control/phase
continuity snapshots. It is absent until a pair is first committed, preserving
historical hashes. A used relationship book can become an empty array; loading
it preserves that distinction from absence. The decoder rejects duplicate
pair identities, overlapping members, unknown fields and malformed versions.
Principal projections expose only the current public counterpart reference as
`paired_with` on battlefield objects; internal pair and incarnation identities
remain authoritative record data.

Composite quoted children use stable `:quote:<slot>` suffixes within the
ordinary granted ability identity. Each child keeps its own semantic key and
activation cost; the parent stores a closed characteristic modifier and the
existing attachment or public-query target. Single-quote identities retain
their historical form. Version-two basic-type addition payloads add an explicit
schema version for the controller's nontoken-creature scope; version-one
land-only payloads retain their exact fields.

A distinct version-two type-replacement handler stores the existing closed
type/color/ability modifier for a single attachment. It uses standard type
operations and independently bound quoted child identities, with no new
GameState field. Version-one composite handlers keep their prior identity and
schema. Correlated subtype cleanup derives from the pinned type vocabulary;
subtypes and supertypes remain separate characteristics.

Layer-six source participation is derived from the current canonical effect
order. Its candidate-source metadata and read-frame caches are not GameState,
checkpoint or projection fields. Replay recomputes them from the same source,
timestamps, modifiers and semantic programs.

The version-four public-state fact codec also admits `source_renowned`, with a
fixed threshold of one and no parameter. Evaluation reads the existing
noncopiable renowned designation rather than counter count. The condition adds
no new per-card state or replay mutation. Explicit graveyard typed-grant
programs retain their source zone and independently registered handler identity;
their granted child retains its battlefield activity and recipient identity.

Granted activated-ability fragments retain their historical version-one,
version-two and Crew version-three payloads. Version four uses the version-two
field set with a closed canonical fixed-output mana spending restriction;
it admits no Crew threshold or additional spending result. The stored
restriction survives grant removal through the ordinary produced-mana
provenance records. Unknown fields, malformed restriction encodings and
unsupported versions reject during decoding.

Resolution-created declaration rules retain their legacy locked-object payload
when they apply to explicit target or source incarnations. Live creature-group
rules use an additive `schema_version: 2` payload with `live_query` and an empty
`locked_objects` list. The query contains any bound controller identity and
public predicate; save/load preserves empty groups so later entrants can match.
The decoder accepts the legacy shape and the exact version-two shape, rejecting
unknown fields, malformed queries, or mixed live and locked recipients. Both
shapes expire through the same continuous-effect cleanup owner.

A resolved all-able-blocker requirement uses the additive exact
`schema_version: 3` shape. Its `requirement` field replaces `restriction` and
contains the historical self-scoped typed requirement payload. It locks exactly
one battlefield incarnation and has no `live_query`. Unknown fields, mixed
restriction and requirement payloads, other requirement kinds and malformed
versions reject. It shares the same until-end-of-turn duration, cleanup,
rollback and replay owner as the older forms.

An optional source untap uses a version-one owned continuation inside the
pending decision. It retains the physical turn and step, immutable participation
plan, public battlefield incarnations, available subjects, and held trigger
snapshots. Reload reissues the controller's capability. An empty selection
declines every optional untap; a stale or malformed continuation fails before
the step commits. Completing the choice preserves held triggers and commits
the selected physical untap set once before advancing to upkeep.

Fixed selection limits use an `untap_selection` version-one continuation in the
same decision kind. It retains the scoped plan, effective public battlefield
snapshot, physical step, and held triggers. Strict canonical comparison rejects
stale identities, control, counters, types, ability presence, or source state
before committing any untap attempt. Reference-array forms expose cumulative
`subset_maxima`, mandatory `maximal_refs`, and distinctness. Historical optional
continuations retain their original shape without an empty limit field.

The closed `state_based_zone_change` replacement continuation retains the
public state-based frame and zone selections, with a separate
`state_based_destruction_selections` array for preceding destruction choices.
Reload resumes both domains before any state-based batch mutation. Wrong
principals, stale frames, missing fields and a batch from the wrong event
domain fail closed. The older `state_based_destruction` payload keeps its
original keys and omits the new array. Runtime trust identity distinguishes
the corrected execution from earlier records.

Control-instruction execution is also bound to the existing runtime-trust
identity. Correcting a same-controller instruction does not rename its readable
descriptor or reset the control-history marker. Records pinned to the prior
execution identity are rejected before commands run; successful decoding is not
evidence that their earlier combat and acquisition results are preserved.

Battlefield-departure look-back history has an additive
`turn_history.departure_history_version` marker. New games set it to one;
the record format binds the same version to the initial checkpoint. Absence
preserves the older journal's event hashes and makes departure predicates
unavailable rather than treating the incomplete history as known empty.
Turn rollover and checkpoint hydration preserve the chosen mode. A historical
record with incompatible runtime trust provenance is explicitly rejected,
not silently recompiled with new predicates.

Attached untap restraints use version-one typed ability fragments with a
closed mode and permanent-type subject. Current giver presence and attachment
determine their applicability. They add no GameState journal or record marker;
ordinary trusted program registration reproduces step and direct-cost checks.
The existing untap-step descriptor does not admit the new internal any-turn
participation relation.

Player-attached characteristic contexts contain a derived enchanted-player
identity read from the current reciprocal attachment. The fixed descriptor
contains no player ID, and no additional GameState field or journal is stored.
Giver-owned attack requirements use a closed version-one fragment. Attached
activation prohibitions add a version-two fragment carrying the attached
relation; version one retains exactly its prior version and scope fields.
Newly generated battlefield triggers serialize the existing current-component
coverage marker. Previously serialized unmarked programs retain their explicit
coverage and the ordinary runtime-trust admission contract.

Disjoint absent-keyword grants use a distinct version-one runtime descriptor
with the existing object-query and keyword-list shapes. Its capability is
required independently during binding. Earlier keyword-grant descriptors keep
their identity and decoding behavior, and the new admission adds no GameState
field or journal.

Characteristic quantity schema six adds an explicit keyword-layer battlefield
count. Its existing scope, predicate and exclusion fields retain their meaning;
the serialized reduction is the fixed object-count mode. Earlier versions keep
their original fields and earlier evaluation boundary. The version supplies no
new GameState field and cannot encode a hidden-zone keyword count or a
count-controlled same-layer ability grant. See
[ADR 0126](../adr/0126-keyword-layer-characteristic-quantities.md).

Bound static declaration rules use a version-one typed fragment containing a
closed relative receiver selector, public giver condition and intrinsic rule
children. Giver and receiver references are derived from current state during
declaration queries. They add no GameState field or resolution journal. The
older characteristic-grant and conditional schema-six descriptors keep their
serialized forms and ordinary historical admission behavior.

Tap-state events use an additive `tap_state_event_version` marker. New games
pin its version in checkpoint and record format. Absence preserves historical
untap-step event contexts and does not emit the expanded tap-state vocabulary.
Current replay requires the marker and runtime trust provenance to agree;
readable descriptors do not independently establish historical execution.
Records pinned to the prior runtime trust identity are explicitly rejected by
the existing manifest-provenance check rather than silently reinterpreted.

Regeneration shields use the same additive compatibility rule. A positive
`CardInstance.regeneration_shields` value is authoritative public
logical-object state; zero is omitted so historical Game Record v3 card payloads
retain their byte representation and hashes. Cleanup and any zone change that
creates a new object clear the value.

## Lifecycle and atomicity

Lifecycle values are `created`, `in_progress`, `paused`, `complete`, `aborted`,
and `corrupt`. A pause has a structured reason. An administrative browser stop
does not add a synthetic game command or advance state; resume may clear only
that administrative reason.

Record components are written atomically and the manifest is the final commit
marker. Accepted network commands are persisted before acknowledgement.
Integrity verification rejects manifest/journal counter disagreement, invalid
hashes, malformed continuations, and incompatible source identity rather than
trusting a stale summary.

## Privacy and derived artifacts

The directory is private operator state, not a pilot, browser, or spectator
API. Checkpoints and journals may contain hidden zones, authoritative object
identity, private choices, or provider audit data. Clients receive only
principal projections. Complete public history is read through the serialized
actor and filtered to a compact spectator-safe event shape.

`review.json`, `review.md`, benchmarks, and hidden-information audits are
derived. They may be reconstructed from the canonical record and must never be
used as replay inputs or edited to change game truth. Provider identity and
usage remain configured, reported, observed, or verified facts; unavailable
values remain unknown rather than estimated as observations.

Derived semantic coverage accepts only current typed preflight and semantic
trust evidence. Activation telemetry joins each recorded ability identity to
the typed activated-ability catalog. Review construction does not infer either
claim from Oracle prose or punctuation, and missing typed evidence remains
unresolved.

## Legacy migration

Legacy snapshot records that lack accepted command payloads cannot be promoted
to honest command replay. Migration uses an explicit `legacy_snapshot` mode,
preserves unavailable decision facts as unavailable, and verifies snapshot
integrity only. It does not convert a historical smoke artifact into rules,
deck, or matchup evidence.

## Commands

```powershell
.\.venv\Scripts\python.exe simctl.py replay run/game `
  --db data/scryfall-current.sqlite3 --verify
.\.venv\Scripts\python.exe simctl.py verify-record run/game `
  --db data/scryfall-current.sqlite3
.\.venv\Scripts\python.exe simctl.py refresh-record run/game `
  --db data/scryfall-current.sqlite3
.\.venv\Scripts\python.exe simctl.py finalize-record run/game `
  --db data/scryfall-current.sqlite3
```

Migration and inspection commands are discoverable through `simctl.py --help`.
Never hand-edit a record to make verification pass. See
[replay architecture](../architecture/replay.md),
[protocol](protocol.md), and [privacy testing](../testing/privacy.md).

Version-three copy exceptions add closed `name`, `card_types` and
`retain_source_stats` fields alongside the existing exception vocabulary.
Preparation resolves retained stats to actual printed/prototype values before
committing copied values. Version-one and version-two payloads omit these
fields and retain their original key sets. The closed grant codec is owned by `granted_activation_model.py`, with its
public import retained through `ability_fragments.py`. Version-three granted
activated fragments add an explicit fixed `crew_threshold`; older grant payloads keep
their prior shape. Copied grants retain their semantic program identity through
pending replacement choices and subsequent copies.

`arrival_history_version: 1` enables current-turn public graveyard-arrival
facts in new games. A `card_arrived_graveyard` event stores the exact arrived
logical incarnation and a closed `origin`; older event kinds omit `origin`.
The history mode persists across reset/rollover and is bound to the initial
checkpoint by manifest state provenance. Older histories omit the mode and
cannot synthesize positive arrival facts. Version-four copy selectors retain
one closed `predicate` beside the existing selection and exception payloads;
older selectors keep their original key sets.

Version-two token-copy recipes add closed `creation_origin: "embalm"` when
produced by that keyword owner. Created tokens retain the internal noncopiable
`embalmed_creation_v1` designation through pending entry choices. Ordinary copy
snapshots omit that designation while retaining resulting copiable values.
Version-five entry-copy selectors add a closed `conditional_exception`; prior
selector and recipe versions retain their original keys. Physical and token
entry results share copied-value validation and commit before observers.

Version-three fixed-color or fixed-card-type Protection fragments add a required
`exempt_aura_incarnation` for one granting Aura. CardProgram attached definitions
carry the closed source marker; live evaluation binds the current source
incarnation before producing the layer-six value. Schemas one and two retain
their original key sets and omit the new field. Other definition owners,
resolution effects, stale or unrelated incarnation pins, and incomplete
version-three payloads fail closed at their respective validation boundary.
Fixed card-type source exceptions require their additional capability at
runtime; the codec and existing attached handler introduce no further record
field or state. Target selection still precedes sacrifice payment.

Source-chosen Aura components reuse the existing public `chosen_color` entry
designation and emit that same version-three fixed-color value. No additional
designation or private-choice state is introduced. Pending entry reload
reissues the entering Aura controller's capability; return and a later entry
retain ordinary new-incarnation semantics and a fresh choice.

Whole-hand result preparations use the closed `discard_hand_result` replacement
intent kind. It retains affected players and private hand member identities so
resumption rejects a changed incarnation or membership. These identities stay
inside the authoritative continuation. Once the discard commits, any suspended
conditional return retains the bound ordinary draw instructions through the
existing completed-choice codec. The result introduces no state or checkpoint
field; earlier whole-hand instructions retain their original representation.

Zone-change additional-cost schema four retains the existing fixed-cost field
set and adds a closed current-power sacrifice meaning. Schemas one through
three keep their prior codecs. A fixed Casualty declaration and paid fact retain
their instance, threshold and selected creature incarnation. Preparation may
serialize that sealed fact before costs are paid; cast-trigger construction
checks it against the ordinary committed cost context. The existing spell-copy
template retains X and targets independently of later source departure. Existing
cast-source-placement modes govern prospective power reads; no new state mode,
checkpoint field or alternate replacement/copy continuation is introduced.

Zone-cost schema five adds a closed `predicate_union` field for alternative
current permanent type/token predicates. Earlier schemas omit and reject that
field. `declared_cast_costs` is a versioned container of source-local instance,
cost kind and boolean declaration choices. It is carried only by casts that
use this representation; existing Kicker and other cost fields retain their
prior codecs. Version-twelve public-condition instructions reference this
container and validate the linked instance before selecting a result.

Original stack-to-battlefield occurrences may include immutable declaration
facts. Older occurrences omit the field and retain their existing fingerprints.
Normalized entry events expose only the chosen instance identifiers. Spell
copies retain declarations through ordinary copy context and copy templates;
ordinary permanent copies do not copy another object's casting history.
Departure and reentry clear the previous object's facts under the existing
zone-state owner. No checkpoint or game-state mode is added.

Activated descriptors may include the optional true `forecast` marker. Older
descriptors omit it. The marker requires hand-zone, owner-upkeep and
once-per-turn metadata and delegates reveal/payment to existing owners.
`forecast_reveal_v1` is an incarnation-bound source annotation containing the
original subject, owner, turn and prior disclosure. Actual hand departure and
non-upkeep step entry end that scope; learned identity remains. A later hand
incarnation cannot inherit the old reveal or usage.

Selected-tap schema two retains the existing field set and adds a closed color
alternative predicate. Its internal plan seals the ability source zone and
logical identity independently of the selected battlefield objects. Schema-one
costs and earlier battlefield source defaults retain their prior meaning.
