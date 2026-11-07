"""Evaluate the life comparison attached to a sealed player attack."""

from collections.abc import Mapping

from ..attack_transition_model import AttackRecipientKind, AttackTransitionEvent
from ..replacement.immutable import thaw_value


ATTACKED_PLAYER_LIFE_FIELD = 'attacked_player_life_is_maximum'
ATTACKED_PLAYER_LIFE_COVERAGE = 'attacked_player_life_intervening'
ATTACKED_PLAYER_LIFE_CAPABILITY = 'trigger.condition.attacked_player_life'


def attacked_player_life_condition():
    return {'all': [
        {'field': 'card', 'op': 'eq', 'value': '$source.ref'},
        {'field': ATTACKED_PLAYER_LIFE_FIELD, 'op': 'truthy', 'value': True},
    ]}


def _contains_life_condition(value):
    if isinstance(value, Mapping):
        return value.get('field') == ATTACKED_PLAYER_LIFE_FIELD or any(
            _contains_life_condition(child) for child in value.values()
        )
    return isinstance(value, (list, tuple)) and any(_contains_life_condition(child) for child in value)


def attacked_player_life_runtime_dependencies(program):
    coverage = set(program.coverage)
    if ATTACKED_PLAYER_LIFE_COVERAGE not in coverage and not _contains_life_condition(program.event_condition):
        return ()
    if (
        program.event != 'creature.attacks' or program.active_zone != 'battlefield'
        or not {'intervening_condition', ATTACKED_PLAYER_LIFE_COVERAGE} <= coverage
        or thaw_value(program.event_condition) != attacked_player_life_condition()
    ):
        raise ValueError('Attacked-player life requires one closed self-attack condition with a resolution recheck')
    return (ATTACKED_PLAYER_LIFE_CAPABILITY,)


def attacked_player_life_holds(host, context, *, source_object_id, controller):
    """Use the original attacked player and the ability controller's live opponents."""
    transition = AttackTransitionEvent.from_dict(context.get('attack_transition'))
    attacker = next((row for row in transition.participants if row.object_id == source_object_id), None)
    assignment = next((row for row in transition.assignments if row.attacker_object_id == source_object_id), None)
    if (
        context.get('event_id') != transition.transition_id or attacker is None or assignment is None
        or attacker.reference != context.get('card') or attacker.controller != context.get('controller')
        or assignment.recipient.reference != context.get('defender')
        or assignment.recipient.defending_player != context.get('defending_player')
    ):
        raise ValueError('Attacked-player life lost its original attack assignment')
    if assignment.recipient.kind is not AttackRecipientKind.PLAYER or controller not in host.active_seats:
        return False
    players = host.state.players
    defender = players.get(assignment.recipient.reference)
    if defender is None:
        raise ValueError('Attacked-player life requires retained facts for the attacked player')
    # CR 800.4i retains the attacked player's life after departure.
    return all(players[seat].life <= defender.life for seat in host.active_seats if seat != controller)
