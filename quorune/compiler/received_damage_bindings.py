"""Exact permanent receiving heads and one attached tap/receiving pair."""
from . import regex_working_set as re

from ..entry_designations import LINKED_PLAYER_RECEIVED_DAMAGE_TEMPLATE
from ..rules.damage_capability_shapes import linked_player_received_damage_condition, linked_player_received_damage_effect
from ..received_damage_model import (
    RECEIVED_DAMAGE_EVENT, RECEIVED_COMBAT_DAMAGE_EVENT, RECEIVED_DAMAGE_MECHANIC,
)
from ..rules.event_subscriptions import FixedEventSubscription, fixed_event_subscription_condition

RECEIVED_PERMANENT_VARIANTS = frozenset({
    'received_any_creature', 'received_enchanted_creature', 'received_equipped_creature',
    'enchanted_creature_tap_or_received_damage',
})
ATTACHED_TAP_RECEIVED_VARIANT = 'enchanted_creature_tap_or_received_damage'
_LINKED_PLAYER_RECEIVING_VARIANT = "source_received_damage_to_linked_player"
_LINKED_PLAYER_RECEIVING = re.compile(
    r"Whenever this creature is dealt damage, (?P<body>it deals that much damage to the chosen player\.)",
    re.I,
)


def linked_player_receiving_result(binding, body):
    if binding.variant != _LINKED_PLAYER_RECEIVING_VARIANT or body != binding.body:
        return None
    return (
        (LINKED_PLAYER_RECEIVED_DAMAGE_TEMPLATE, (linked_player_received_damage_effect(),), None,
         ("cr-120-damage",)), True,
    )


def _creature_condition(*, attached=False, received=True):
    conditions = []
    if attached:
        conditions.append({'field': 'source_attachment_target_ref', 'op': 'eq', 'value': '$context.card'})
    if received:
        conditions.append({'field': 'target_kind', 'op': 'eq', 'value': 'permanent'})
    conditions.append({'field': 'target_types' if received else 'types', 'op': 'contains_any', 'value': ['creature']})
    return {'all': conditions}


def received_permanent_binding_spec(material_line: str):
    from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec
    if linked := _LINKED_PLAYER_RECEIVING.fullmatch(material_line):
        return FixedPublicEventBindingSpec(
            event=RECEIVED_DAMAGE_EVENT, variant=_LINKED_PLAYER_RECEIVING_VARIANT, body=linked["body"],
            template_id=LINKED_PLAYER_RECEIVED_DAMAGE_TEMPLATE, mechanic=RECEIVED_DAMAGE_MECHANIC,
            condition=linked_player_received_damage_condition(),
        )
    pair = re.fullmatch(
        r'(?:When|Whenever) enchanted creature becomes tapped or is dealt damage, (?P<body>.+)',
        material_line, re.I,
    )
    if pair is not None:
        subscriptions = (
            FixedEventSubscription('permanent.tap', _creature_condition(attached=True, received=False)),
            FixedEventSubscription(RECEIVED_DAMAGE_EVENT, _creature_condition(attached=True)),
        )
        return FixedPublicEventBindingSpec(
            event='permanent.tap', variant=ATTACHED_TAP_RECEIVED_VARIANT, body=pair['body'],
            template_id='fixed-counter-public-multi-event-trigger-v1', mechanic=RECEIVED_DAMAGE_MECHANIC,
            condition=fixed_event_subscription_condition(subscriptions),
            capabilities=('trigger.event.normalized_public_action', 'trigger.subscription.fixed_multi_event',
                          'attachment.reference.current_or_lki'),
        )
    match = re.fullmatch(
        r'(?:When|Whenever) (?P<subject>a creature|enchanted creature|equipped creature) '
        r'is dealt (?P<combat>combat )?damage, (?P<body>.+)', material_line, re.I,
    )
    if match is None:
        return None
    subject = match['subject'].casefold()
    relation = 'any' if subject == 'a creature' else subject.split()[0]
    return FixedPublicEventBindingSpec(
        event=RECEIVED_COMBAT_DAMAGE_EVENT if match['combat'] else RECEIVED_DAMAGE_EVENT,
        variant='received_' + relation + '_creature', body=match['body'],
        template_id='fixed-counter-public-damage-trigger-v1', mechanic=RECEIVED_DAMAGE_MECHANIC,
        condition=_creature_condition(attached=relation != 'any'),
        capabilities=('attachment.reference.current_or_lki',) if relation != 'any' else (),
    )
