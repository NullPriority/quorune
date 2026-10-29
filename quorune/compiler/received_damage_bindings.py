"""Exact permanent receiving heads and one attached tap/receiving pair."""
import re

from ..received_damage_model import (
    RECEIVED_DAMAGE_EVENT, RECEIVED_COMBAT_DAMAGE_EVENT, RECEIVED_DAMAGE_MECHANIC,
)
from ..rules.event_subscriptions import FixedEventSubscription, fixed_event_subscription_condition

RECEIVED_PERMANENT_VARIANTS = frozenset({
    'received_any_creature', 'received_enchanted_creature', 'received_equipped_creature',
    'enchanted_creature_tap_or_received_damage',
})
ATTACHED_TAP_RECEIVED_VARIANT = 'enchanted_creature_tap_or_received_damage'


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
