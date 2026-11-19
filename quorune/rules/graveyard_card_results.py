"""Read a specific exile result and emit ordinary counter/life follow-ups."""
from ..errors import GameRuleError
from ..graveyard_card_result_model import GraveyardCardCounterLifeSpec, GRAVEYARD_CARD_COUNTER_LIFE_DEPENDENCIES
from ..object_query import ObjectQueryResult
from ..public_zone_moves import PublicZoneMoveError, exile_public_graveyard_card
from ..semantic_runtime.graveyard_card_result_intent import GraveyardCardCounterLifeIntent
from ..zone_transition_model import PUBLIC_ZONES
from .committed_quantity_followups import CommittedQuantityFollowup, fixed_quantity_effect


def _facts(host, card):
    row = host._public_object_query_result(card)
    if (not isinstance(row, ObjectQueryResult) or row.object_id != card.object_id
            or row.logical_object_id != card.logical_object_id or row.known_to_actor is not True or row.token):
        raise GameRuleError('Graveyard card result requires current public physical-card facts')
    return row


def _source_available(host, intent):
    source = host.state.cards.get(intent.source_object_id)
    return (source is not None and source.zone == 'battlefield' and not source.phased_out
        and source.logical_object_id == intent.source_logical_object_id)


def commit_graveyard_card_counter_life(host, intent):
    if not isinstance(intent, GraveyardCardCounterLifeIntent):
        raise GameRuleError('Targeted graveyard card result requires its typed intent')
    host._require_seat(intent.actor, in_game=True)
    card = host._resolve_object(intent.actor, intent.object_ref, zones={'graveyard'})
    if card.zone != 'graveyard' or not card.is_card_object:
        raise GameRuleError('Targeted graveyard card result requires one current physical graveyard card')
    before = _facts(host, card)
    try:
        exile_public_graveyard_card(host, intent.object_ref, actor=intent.actor,
            reason=intent.reason, replacement_selections=intent.replacement_selections)
    except PublicZoneMoveError as exc:
        raise GameRuleError(str(exc)) from exc
    current = host.state.cards.get(card.object_id)
    if current is None or not current.is_card_object:
        raise GameRuleError('Targeted physical card disappeared while its result was resolving')
    row = _facts(host, current) if current.zone in PUBLIC_ZONES and not current.face_down else before
    if not intent.result.card_form.matches(types=set(row.types), subtypes=set(row.subtypes), supertypes=set(row.supertypes)):
        return CommittedQuantityFollowup(())
    effects = []
    if _source_available(host, intent):
        effects.append(fixed_quantity_effect({'op': 'place_counters', 'card': '$source.zone_object',
            'counter': '+1/+1', 'amount': 1, 'source': '$source'}))
    effects.append(fixed_quantity_effect({'op': 'life', 'player': '$controller', 'delta': 1}))
    return CommittedQuantityFollowup(tuple(effects))


def graveyard_card_counter_life_payload_dependencies(value):
    from collections.abc import Mapping
    from ..graveyard_card_result_model import GRAVEYARD_CARD_COUNTER_LIFE_OPERATION
    if not isinstance(value, Mapping) or value.get('op') != GRAVEYARD_CARD_COUNTER_LIFE_OPERATION:
        return ()
    GraveyardCardCounterLifeSpec.from_effect(value)
    return GRAVEYARD_CARD_COUNTER_LIFE_DEPENDENCIES


def graveyard_card_counter_life_node_capabilities(*, effects, target_schema, **kwargs):
    from collections.abc import Mapping
    from ..replacement.immutable import FrozenMap
    from .graveyard_card_targets import PublicGraveyardCardTargetSpec, PublicGraveyardCardTargetKind
    expected = PublicGraveyardCardTargetSpec(PublicGraveyardCardTargetKind.CARD, owner_relation='any').to_target_schema()
    if (len(effects) != 1 or not isinstance(target_schema, Mapping) or type(target_schema.get('count')) is not int
            or FrozenMap(target_schema) != FrozenMap(expected)):
        return ()
    try:
        GraveyardCardCounterLifeSpec.from_effect(effects[0])
    except (ValueError, TypeError, KeyError):
        return ()
    return GRAVEYARD_CARD_COUNTER_LIFE_DEPENDENCIES
