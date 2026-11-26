from __future__ import annotations

"""Closed source and controlled-creature target occurrence subscriptions."""

from . import regex_working_set as re

from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from ..rules.stack_controller_payment_shapes import (
    controlled_sliver_opponent_target_announcement_condition,
    target_announcement_payment_condition_is_closed,
)
from ..rules.target_announcements import TARGET_ANNOUNCEMENT_CAPABILITY, TARGET_ANNOUNCEMENT_EVENT
from .fixed_public_event_trigger_bindings import _spec


_TRIGGER = re.compile(
    r'^(?:When|Whenever) (?P<subject>.+?) becomes the target of a '
    r'(?P<kind>spell or ability|spell|ability)(?P<opponent> an opponent controls)?, (?P<body>.+)$', re.I,
)
_FIXED_COUNTER_PAYMENT = re.compile(
    r'counter that spell or ability unless its controller pays \{2\}\.?', re.I,
)


def target_announcement_result(binding, body: str, *, card_name: str) -> str:
    if binding.variant in {'source_target_announcement', 'heroic_source_targeted'}:
        body = re.sub(r'\bon it\b', 'on ' + card_name, body, flags=re.I)
        body = re.sub(r'^it ', card_name + ' ', body, flags=re.I)
    return body


def target_announcement_source_sacrifice(binding, body: str, *, card_name: str):
    if binding.variant != 'source_target_announcement' or not re.fullmatch(
        r'sacrifice (?:it|this (?:creature|artifact|enchantment|permanent)|' +
        SourceReferenceSpec(card_name).regex_pattern + r')\.?', body, re.I,
    ):
        return None
    from ..source_maintenance import SourceMaintenanceSpec, SOURCE_MAINTENANCE_MECHANIC
    from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
    return ('fixed-targeted-source-sacrifice-v1', (SourceMaintenanceSpec().effect(),), None,
        (SOURCE_MAINTENANCE_MECHANIC, FIXED_EFFECT_PAYMENT_MECHANIC))


def target_announcement_bound_result(binding, body: str, *, card_name: str):
    if (binding.variant in {'source_target_announcement', 'public_target_announcement'}
            and _FIXED_COUNTER_PAYMENT.fullmatch(body)
            and target_announcement_payment_condition_is_closed(binding.public_condition)):
        from ..rules.stack_controller_payment_cost import (
            STACK_CONTROLLER_PAYMENT_MECHANIC, target_announcement_counter_payment_effect,
        )
        return (
            'target-announcement-counter-unless-pay-v1',
            (target_announcement_counter_payment_effect(),),
            None,
            ('counter', STACK_CONTROLLER_PAYMENT_MECHANIC),
        )
    return target_announcement_source_sacrifice(binding, body, card_name=card_name)


def _target_announcement_spec(body, *, condition, source_targeted=False):
    return _spec(
        TARGET_ANNOUNCEMENT_EVENT, 'source_target_announcement' if source_targeted else 'public_target_announcement', body,
        'fixed-counter-target-announcement-trigger-v1', 'trigger-event-normalized-target-announcement',
        condition=condition, capabilities=(TARGET_ANNOUNCEMENT_CAPABILITY,),
    )


def target_announcement_binding_spec(material_line: str, *, card_name: str | None = None):
    match = _TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    subject = match['subject'].casefold()
    if subject == 'a sliver creature you control':
        if (match['kind'].casefold() != 'spell or ability' or not match['opponent']
                or not _FIXED_COUNTER_PAYMENT.fullmatch(match['body'])):
            return None
        return _target_announcement_spec(
            match['body'], condition=controlled_sliver_opponent_target_announcement_condition(),
        )
    conditions = []
    source_targeted = False
    if source_self_permanent_type(subject) is not None or card_name and SourceReferenceSpec(card_name).matches(match['subject']):
        source_targeted = True
        conditions.append({'field': 'card', 'op': 'eq', 'value': '$source.ref'})
    elif subject in {'a creature you control', 'a creature or planeswalker you control'}:
        conditions.extend((
            {'field': 'controller', 'op': 'eq', 'value': '$source.controller'},
            {'field': 'types', 'op': 'contains_any', 'value': ['creature', 'planeswalker'] if 'planeswalker' in subject else ['creature']},
        ))
    else:
        return None
    if not source_targeted and re.search(r'\b(?:it|its|that creature|that permanent)\b', match['body'], re.I):
        return None
    kinds = {'spell': ['spell', 'spell_copy'], 'ability': ['activated_ability', 'triggered_ability']}
    if match['kind'].casefold() in kinds:
        conditions.append({'field': 'stack_kind', 'op': 'in', 'value': kinds[match['kind'].casefold()]})
    if match['opponent']:
        conditions.append({'field': 'stack_controller', 'op': 'ne', 'value': '$source.controller'})
    return _target_announcement_spec(
        match['body'], condition={'all': conditions}, source_targeted=source_targeted,
    )
