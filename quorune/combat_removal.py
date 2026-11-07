"""One printed combat-removal instruction and its ordered untap."""

from dataclasses import dataclass
from typing import Literal

COMBAT_REMOVAL_OPERATION = 'remove_from_combat'
COMBAT_REMOVAL_CAPABILITY = 'combat.effect.remove_from_combat'
COMBAT_REMOVAL_MECHANIC = 'fixed-combat-removal'
BLOCKED_ATTACKER_REFERENCE = '$event.permanent_object'


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
