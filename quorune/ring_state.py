"""Typed Ring progress and bearer-choice ownership on the public emblem object.

This state owner does not by itself register Ring gameplay or emblem abilities.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Protocol

from .characteristic_evaluation import type_parts
from .continuous_effect_model import Layer
from .control_effects import source_continuity_snapshot
from .ring_model import (
    RingBearerChoice, RingBearerChoicePlan, RingBearerSnapshot, RingEmblemState,
    RingObjectIdentity, RingStateError,
    RING_TEMPTATION_OPERATION, RingRuleView,
)
from .source_continuity import SourceContinuityHistory, SourceContinuitySnapshot


RING_STATE_ANNOTATION = 'ring_state_v1'
_RING_DISPLAY_ABILITIES = (
    'Your Ring-bearer is legendary and cannot be blocked by creatures with greater power.',
    'After two temptations: whenever your Ring-bearer attacks, draw a card, then discard a card.',
    'After three temptations: each creature blocking your Ring-bearer is sacrificed by its controller at end of combat.',
    'After four temptations: whenever your Ring-bearer deals combat damage to a player, each opponent loses 3 life.',
)


class RingStateHost(Protocol):
    state: Any
    active_seats: list[str]

    def _effective_card_data(self, card: Any, *, maximum_layer: Layer) -> Mapping[str, Any]: ...
    def create_emblem(self, owner: str, **kwargs: Any) -> str: ...
    def _log(self, *args: Any, **kwargs: Any) -> Any: ...


def _identity(card: Any) -> RingObjectIdentity:
    return RingObjectIdentity(card.object_id, card.ref, card.logical_object_id)


def _active_player(host: RingStateHost, player: str) -> None:
    if (type(player) is not str or player not in host.active_seats
            or player not in host.state.players):
        raise RingStateError('Ring progress requires an active player')


def _history(card: Any) -> SourceContinuitySnapshot | None:
    history = card.source_continuity
    if history is None:
        return None
    if not isinstance(history, SourceContinuityHistory):
        raise RingStateError('Bearer continuity facts are malformed')
    return history.snapshot()


def _retained_source(host: RingStateHost, player: str) -> tuple[Any, RingEmblemState] | None:
    _active_player(host, player)
    found = []
    for object_id in host.state.players[player].zones['command']:
        card = host.state.cards.get(object_id)
        if card is None:
            raise RingStateError('The command zone contains an unknown object')
        if RING_STATE_ANNOTATION not in card.annotations:
            continue
        state = RingEmblemState.from_dict(card.annotations[RING_STATE_ANNOTATION])
        if (card.object_kind != 'emblem' or card.zone != 'command'
                or card.owner != player or card.controller != player
                or state.player != player or state.emblem != _identity(card)):
            raise RingStateError('Ring state does not match its owned command-zone emblem')
        found.append((card, state))
    if len(found) > 1:
        raise RingStateError('A player cannot have multiple retained Ring emblems')
    return found[0] if found else None


def _current_state(host: RingStateHost, state: RingEmblemState) -> RingEmblemState:
    bearer = state.bearer
    if bearer is None:
        return state
    card = host.state.cards.get(bearer.identity.object_id)
    if (card is None or card.zone != 'battlefield' or card.controller != state.player
            or _identity(card) != bearer.identity):
        return state.without_bearer()
    history = _history(card)
    if history is None:
        raise RingStateError('A retained bearer has no control continuity facts')
    # Phasing, untapping and ceasing to be a creature do not end this designation.
    return state if bearer.control_is_continuous(history) else state.without_bearer()


def current_ring_state(host: RingStateHost, player: str) -> RingEmblemState | None:
    """Return immutable current facts, suppressing an expired old designation."""
    source = _retained_source(host, player)
    return _current_state(host, source[1]) if source is not None else None


def current_ring_rule_views(host: RingStateHost) -> tuple[RingRuleView, ...]:
    result = []
    for player in host.active_seats:
        source = _retained_source(host, player)
        if source is not None:
            result.append(RingRuleView(_current_state(host, source[1]), source[0].zone_timestamp))
    return tuple(result)


def pending_ring_choice_plan(host: RingStateHost, player: str) -> RingBearerChoicePlan:
    """Materialize immutable progress and live options for a prepared temptation."""
    source = _retained_source(host, player)
    if source is None:
        raise RingStateError('A Ring choice requires prepared emblem progress')
    return RingBearerChoicePlan(source[1], _candidates(host, player))


def materialized_ring_choice_plans(host: RingStateHost, player: str, effect: Mapping[str, Any]):
    if effect.get('op') != RING_TEMPTATION_OPERATION:
        return {}
    stage = effect.get('_ring_stage')
    if stage is not None and type(stage) is not str:
        raise RingStateError('Ring choice stage must be exact text')
    if stage not in {'choose','commit'}:
        return {}
    if effect.get('player') != player:
        raise RingStateError('Ring choice actor binding changed')
    return {player: pending_ring_choice_plan(host, player).to_dict()}


def _candidates(host: RingStateHost, player: str) -> tuple[RingBearerSnapshot, ...]:
    result = []
    for card in host.state.cards.values():
        if card.zone != 'battlefield' or card.controller != player or card.phased_out:
            continue
        data = host._effective_card_data(card, maximum_layer=Layer.TYPE)
        if not isinstance(data, Mapping) or type(data.get('type_line')) is not str:
            raise RingStateError('A bearer choice requires known current creature types')
        if 'creature' not in type_parts(data['type_line'])[0]:
            continue
        result.append(RingBearerSnapshot(
            _identity(card), _history(card) or SourceContinuitySnapshot(0, 0, 0)))
    for field in ('object_id', 'object_ref', 'logical_object_id'):
        values = [getattr(candidate.identity, field) for candidate in result]
        if len(values) != len(set(values)):
            raise RingStateError('Current bearer candidates must be distinct known objects')
    return tuple(sorted(result, key=lambda candidate: candidate.identity.object_ref))


def prepare_ring_temptation(
    host: RingStateHost, *, player: str, reason: str,
) -> RingBearerChoicePlan:
    """Commit known progress before the non-target choice; emit no completed event."""
    _active_player(host, player)
    if type(reason) is not str or not reason.strip():
        raise RingStateError('Ring progress requires its effect provenance')
    source = _retained_source(host, player)
    previous = _current_state(host, source[1]) if source is not None else None
    candidates = _candidates(host, player)
    if source is None:
        ref = host.create_emblem(player, abilities=_RING_DISPLAY_ABILITIES,
                                 display_label='The Ring', reason=reason)
        created = [host.state.cards[oid] for oid in host.state.players[player].zones['command']
                   if host.state.cards[oid].ref == ref]
        if len(created) != 1:
            raise RingStateError('Emblem creation did not return one command-zone object')
        emblem = created[0]
        if (emblem.object_kind != 'emblem' or emblem.zone != 'command'
                or emblem.owner != player or emblem.controller != player):
            raise RingStateError('Emblem creation returned the wrong public object')
        state = RingEmblemState(player, _identity(emblem), 1)
    else:
        emblem = source[0]
        state = previous.tempted_again()
    plan = RingBearerChoicePlan(state, candidates)
    emblem.annotations[RING_STATE_ANNOTATION] = state.to_dict()
    host._log(player, 'ring.temptation.begin', 'The Ring temptation awaits its creature choice.',
              {'emblem': emblem.ref, 'temptation_count': state.temptation_count, 'reason': reason},
              importance=1, changed_objects=[emblem.object_id], changed_players=[player])
    return plan


def commit_ring_bearer_choice(
    host: RingStateHost, plan: RingBearerChoicePlan, selected_ref: str | None,
) -> RingBearerChoice:
    """Revalidate a sealed public choice before writing its designated incarnation."""
    if not isinstance(plan, RingBearerChoicePlan):
        raise RingStateError('A bearer commit requires a typed choice plan')
    if selected_ref is not None and type(selected_ref) is not str:
        raise RingStateError('The chosen bearer requires an exact public reference')
    source = _retained_source(host, plan.state.player)
    if source is None or source[1] != plan.state:
        raise RingStateError('The pending Ring progress has changed')
    emblem, retained = source
    state = _current_state(host, retained)
    candidates = _candidates(host, state.player)
    if selected_ref is None:
        result = RingBearerChoicePlan(state, candidates).select(None)
    else:
        offered = next((row for row in plan.candidates if row.identity.object_ref == selected_ref), None)
        if offered is None:
            raise RingStateError('The bearer reference was not offered')
        plan.select(offered)
        current = next((row for row in candidates if row.identity == offered.identity), None)
        if current is None or not offered.control_is_continuous(current.history):
            raise RingStateError('The selected creature or its control continuity changed')
        card = host.state.cards[current.identity.object_id]
        # Initialize retained history only for the validated, actually chosen object.
        captured = source_continuity_snapshot(card)
        selected = RingBearerSnapshot(current.identity, captured)
        result = RingBearerChoice(replace(state, bearer=selected), selected)
    emblem.annotations[RING_STATE_ANNOTATION] = result.state.to_dict()
    host._log(state.player, 'ring.bearer.choice_complete', 'The Ring bearer choice completed.',
              {'emblem': emblem.ref, 'creature_chosen': result.creature_chosen,
               'bearer': result.chosen_bearer.identity.object_ref if result.creature_chosen else None,
               'temptation_count': result.state.temptation_count},
              importance=1, changed_objects=[emblem.object_id], changed_players=[state.player])
    return result
