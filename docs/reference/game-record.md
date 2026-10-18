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

Game Record v3 is the authoritative persistence, replay, and audit directory for
one game. It separates canonical state and accepted inputs from delivery state
and derived review. The schemas under `schemas/game-record-v3-*.schema.json`
and the serializer implementation define the exact format.

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

Fixed additional tap prices record `fixed_additional_tap_payment` in the
selected casting option. Its closed version-one frame contains the compiled
price and each selected physical object, logical incarnation and reference.
The ordinary tap-cost reference list also contains any Convoke or Improvise
payment; the fixed reservation distinguishes those resources when validating
Delve and mana remainders. Save/load revalidates the original reservation rather
than replacing it with a new selection. Existing casting options omit this
frame and retain their serialized form.

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

Resolution-created declaration rules retain their legacy locked-object payload
when they apply to explicit target or source incarnations. Live creature-group
rules use an additive `schema_version: 2` payload with `live_query` and an empty
`locked_objects` list. The query contains any bound controller identity and
public predicate; save/load preserves empty groups so later entrants can match.
The decoder accepts the legacy shape and the exact version-two shape, rejecting
unknown fields, malformed queries, or mixed live and locked recipients. Both
shapes expire through the same continuous-effect cleanup owner.

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
