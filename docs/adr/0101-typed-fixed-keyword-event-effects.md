---
title: "ADR 0101: typed fixed keyword event effects"
status: "ADR"
authoritative_source: "fixed printed keyword grammar, normalized public events, typed semantic choices, and canonical effect owners"
verified: "2026-09-21"
audience: "compiler and rules contributors"
maintenance: "hand-maintained"
adr_id: "0101"
decision_status: "accepted"
date: "2026-09-21"
---

# ADR 0101: typed fixed keyword event effects

## Context

The current frontier contains a coherent group of isolated fixed Afflict,
Annihilator, Firebending, Ingest, Mobilize, and Soulshift keyword lines. Their
events already have authoritative normalized producers, and most results
already have typed effect, choice, target, token, zone, mana, APNAP,
projection, and replay owners. Treating the six lines as generic continuous
text leaves a measured 52 complete cards and 86 abilities residual.

Two result shapes were not expressible through the registered semantic
operation vocabulary. Ingest needs the damaged player's current library top,
which is not a target and must remain hidden until the zone move commits.
Mobilize needs one public attack destination for each token before its
replacement-aware simultaneous creation.

## Decision

Add two closed operations and keep all authoritative mutation in existing
owners.

- `exile_top_library_card` accepts only one active player. Its handler reads
  that player's current top card and delegates the move, replacements,
  normalized zone events, projection, and replay to `ZoneTransitionOwner`.
- `choose_attacking_token_destinations` is a principal-scoped semantic choice.
  It accepts only the fixed Mobilize Warrior definition and a quantity from
  one through twenty, derives legal players, opposing planeswalkers, and
  opponent-protected Battles from public typed state, then emits one
  `CreateTokenIntent` with exact per-token destinations.
- Token intent identity includes tapped state and attack assignments so a
  replacement suspension, save/load, or replay cannot silently turn Mobilize
  tokens into ordinary entrants. Historical token identities default those
  additive fields to false and empty.
- Firebending retention is an additive property of the existing mana-
  provenance lot. Payment consumes ordinary eligible mana before retained
  mana, and step clearing preserves only the actual unspent retained lot until
  end of combat.
- Afflict, Annihilator, and Mobilize consume the defending player sealed by
  the attack or block transition. Ingest consumes committed positive combat
  damage to a player. Soulshift consumes battlefield-departure LKI and the
  existing own-graveyard target and optional-return owners.
- Every trigger uses the shared current layer-6 component-presence query.

## Alternatives

- Parse reminder text at runtime. Rejected because current-game Oracle prose
  is not rules authority and would bypass compiler source spans and capability
  closure.
- Add a second token or zone engine. Rejected because the existing replacement
  and zone-transition owners already provide ordering, rollback, and replay.
- Track Firebending as a color count outside mana provenance. Rejected because
  spending retained mana and later adding ordinary mana of the same color
  would preserve the wrong mana.
- Encode Mobilize destinations as one shared target. Rejected because CR 508.4
  permits each created attacking token to attack a different legal recipient.
- Admit variable, combined, granted, copied, or conditional keyword forms.
  Rejected because their quantity or ability-presence boundaries are outside
  this fixed printed family.

## Consequences

The registered universal-operation inventory grows by exactly
`exile_top_library_card` and `choose_attacking_token_destinations`. Neither
operation writes `GameState` directly: the former delegates to the canonical
zone owner, and the latter produces a typed token intent. CommanderEngine,
direct and unowned writes, runtime Oracle access, card-identity dispatch, and
oversized production symbols do not grow.

The admitted grammar remains isolated fixed printed keywords only. Variable,
repeated, combined, granted, copied, text-changed, conditional, team-specific,
and independently incomplete forms remain source-spanned residuals. Future
operations require a separate architecture review rather than extending this
allowance.
