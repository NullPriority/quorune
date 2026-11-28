"""One printed combat-removal instruction and its ordered untap."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

COMBAT_REMOVAL_OPERATION = 'remove_from_combat'
COMBAT_REMOVAL_CAPABILITY = 'combat.effect.remove_from_combat'
COMBAT_REMOVAL_MECHANIC = 'fixed-combat-removal'
BLOCKED_ATTACKER_REFERENCE = '$event.permanent_object'
OPTIONAL_BLOCKED_ATTACKER_RETURN_VERSION = 5
CONTROLLED_BLOCKED_ATTACKER = {'field': 'controller', 'op': 'eq', 'value': '$source.controller'}


def optional_blocked_attacker_return_effect():
    return {
        'op': 'offer_optional_effect', 'player': '$controller',
        'schema_version': OPTIONAL_BLOCKED_ATTACKER_RETURN_VERSION,
        'effects': [{'op': 'bounce', 'card': BLOCKED_ATTACKER_REFERENCE}],
    }


def is_optional_blocked_attacker_return(effect):
    return (isinstance(effect, Mapping) and effect.get('op') == 'offer_optional_effect'
            and effect.get('schema_version') == OPTIONAL_BLOCKED_ATTACKER_RETURN_VERSION)


def blocked_attacker_return_reference(effect, *, actor, resolved=False):
    children = effect.get('effects')
    if (not is_optional_blocked_attacker_return(effect)
            or set(effect) != {'op', 'player', 'schema_version', 'effects'}
            or type(effect['schema_version']) is not int or effect['player'] != actor
            or not isinstance(children, (list, tuple)) or len(children) != 1
            or not isinstance(children[0], Mapping)):
        raise ValueError('Expected one versioned optional blocked attacker return')
    bounce = children[0]
    if set(bounce) != {'op', 'card'} or bounce['op'] != 'bounce':
        raise ValueError('The blocked attacker return lost its owner-hand instruction')
    ref = bounce['card']
    if resolved:
        if ref is not None and (type(ref) is not str or not ref):
            raise ValueError('Expected the original blocked attacker reference')
    elif ref != BLOCKED_ATTACKER_REFERENCE:
        raise ValueError('The optional return must use the original blocked attacker')
    return ref


def prepare_optional_blocked_attacker_return(host, item, raw_effect, effect):
    if not is_optional_blocked_attacker_return(raw_effect):
        return effect
    blocked_attacker_return_reference(raw_effect, actor='$controller')
    ref = blocked_attacker_return_reference(effect, actor=item.controller, resolved=True)
    card = next((row for row in host.state.cards.values() if row.ref == ref), None)
    if card is None or card.zone != 'battlefield' or card.phased_out:
        return None
    return {key: value for key, value in effect.items() if key != 'schema_version'}


@dataclass(frozen=True, slots=True)
class CombatRemovalIntent:
    actor: str
    object_ref: str
    untap: Literal['before', 'after'] | None
    reason: str


def combat_removal_instruction(effect):
    if (set(effect) not in ({'op', 'card'}, {'op', 'card', 'untap'})
            or effect['op'] != COMBAT_REMOVAL_OPERATION
            or type(effect['card']) is not str or not effect['card']
            or 'untap' in effect and effect['untap'] not in ('before', 'after')):
        raise ValueError('Combat removal requires one object and an optional ordered untap')
    return effect['card'], effect.get('untap')


def execute_combat_removal(host, intent: CombatRemovalIntent):
    from .tap_state import set_permanent_tapped

    card = host._resolve_object(intent.actor, intent.object_ref, zones={'battlefield'})
    if card.phased_out:
        return False
    identity = card.logical_object_id

    def untap():
        set_permanent_tapped(host, card.ref, actor=intent.actor, tapped=False,
                             reason=intent.reason, logical_object_id=identity)

    if intent.untap == 'before':
        untap()
    current = host.state.cards.get(card.object_id)
    if current is None or current.zone != 'battlefield' or current.phased_out or current.logical_object_id != identity:
        return False
    removed = host._remove_object_from_combat(current, reason=intent.reason)
    if intent.untap == 'after':
        untap()
    return removed


def unavailable_optional_combat_removal(host, descriptor, effect):
    if (set(descriptor) != {'op', 'player', 'effects'} or descriptor['op'] != 'offer_optional_effect'
            or descriptor['player'] != '$controller'):
        return False
    children = descriptor['effects']
    if not isinstance(children, (list, tuple)) or tuple(children) != ({
        'op': COMBAT_REMOVAL_OPERATION, 'card': BLOCKED_ATTACKER_REFERENCE, 'untap': 'before',
    },):
        return False
    ref = effect['effects'][0]['card']
    card = next((card for card in host.state.cards.values() if card.ref == ref), None)
    if card is None or card.zone != 'battlefield' or card.phased_out:
        return True
    if card.attacking is not None or card.blocking is not None:
        return False
    from .rules.untap_permissions import untap_is_permitted

    return not card.tapped or not untap_is_permitted(host, card)
