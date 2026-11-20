"""Apply the paid/declined result after the cost through canonical zone movement."""
from collections.abc import Mapping
from ..rules.recover import validate_recover_result
from ..semantic_runtime.context import SemanticNodeError
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from ..zone_trigger_events import ZoneTransitionKind


def apply_recover_source_result(host, effect, actor, *, operation=None, reason=None):
    try:
        validate_recover_result(effect, resolved=True)
    except (ValueError, TypeError, KeyError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    source = effect.get('_runtime_source')
    if (operation not in (None, effect['op']) or effect['player'] != actor or not isinstance(source, Mapping)
            or type(source.get('object_id')) is not str or not source['object_id']
            or source.get('logical_object_id') != effect['identity']):
        raise SemanticNodeError('Recover result lost its authoritative source binding')
    card = host.state.cards.get(source['object_id'])
    if (card is None or not card.is_card_object or card.zone != 'graveyard' or card.logical_object_id != effect['identity']
            or card.ref != effect['card']):
        return ()
    if effect['destination'] == 'hand' and card.owner != actor:
        return ()
    intent = MoveObjectsSimultaneouslyIntent(actor=actor, object_refs=(card.ref,), expected_zones=('graveyard',),
        destination=effect['destination'], reason=reason or effect.get('reason') or 'Recover',
        transition_kind=ZoneTransitionKind.ORDINARY,
        replacement_selections=tuple(effect.get('_replacement_selections') or ()))
    return host.move_objects_simultaneously_intent(intent)
