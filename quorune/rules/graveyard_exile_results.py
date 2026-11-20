"""Observe a specific chosen card only after its canonical move commits."""
from ..errors import GameRuleError
from ..object_query import ObjectQueryResult
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.graveyard_exile_result_intent import GraveyardExileLifeResultIntent
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from ..zone_transition_model import PUBLIC_ZONES
from .committed_quantity_followups import quantity_followup_from_count
from ..graveyard_exile_result_model import GraveyardExileLifeResultSpec, GRAVEYARD_EXILE_LIFE_RESULT_CAPABILITY


def graveyard_exile_life_result_dependencies(result):
    if result is None:
        return ()
    if not isinstance(result, GraveyardExileLifeResultSpec):
        raise ValueError('Selected-card result dependency requires its typed consequence')
    return GRAVEYARD_EXILE_LIFE_RESULT_CAPABILITY, 'life.change.effect'


def _public_facts(host, card):
    facts = host._public_object_query_result(card)
    if (not isinstance(facts, ObjectQueryResult) or facts.object_id != card.object_id
            or facts.logical_object_id != card.logical_object_id or not facts.known_to_actor or facts.token):
        raise GameRuleError('Selected graveyard result requires current public physical-card facts')
    return facts


def commit_graveyard_exile_life_result(host, intent):
    if not isinstance(intent, GraveyardExileLifeResultIntent):
        raise GameRuleError('Selected graveyard result requires its typed intent')
    host._require_seat(intent.actor, in_game=True)
    card = host._resolve_object(intent.actor, intent.object_refs[0], zones={'graveyard'}, owned_only=True)
    if (card.zone != 'graveyard' or card.owner != intent.actor or not card.is_card_object
            or card.logical_object_id != intent.selected_identity):
        raise GameRuleError('Selected graveyard result card changed its owner, zone, or incarnation')
    last_known = _public_facts(host, card)
    base = MoveObjectsSimultaneouslyIntent(actor=intent.actor, object_refs=intent.object_refs,
        expected_zones=('graveyard',), destination='exile', reason=intent.reason, owned_only=True,
        replacement_selections=intent.replacement_selections)
    from ..semantic_choices.simultaneous_movement import commit_simultaneous_movement
    commit_simultaneous_movement(host, base)
    current = host.state.cards.get(card.object_id)
    if current is None or not current.is_card_object:
        raise GameRuleError('Selected physical card disappeared during its result instruction')
    facts = _public_facts(host, current) if current.zone in PUBLIC_ZONES and not current.face_down else last_known
    matches = intent.result.card_form.matches(types=set(facts.types), subtypes=set(facts.subtypes),
        supertypes=set(facts.supertypes))
    return quantity_followup_from_count(FrozenMap({'op': 'life', 'player': '$controller',
        'delta': intent.result.amount}), int(matches))
