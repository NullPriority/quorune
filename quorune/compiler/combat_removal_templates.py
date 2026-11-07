"""Exact printed combat-removal instructions and becoming-blocked results."""

import re

from ..combat_removal import (
    BLOCKED_ATTACKER_REFERENCE, COMBAT_REMOVAL_MECHANIC, COMBAT_REMOVAL_OPERATION,
)
from .direct_target import direct_permanent_target_spec
from .optional_effect_templates import fixed_optional_effect_template


def combat_removal_effect_template(text):
    match = re.fullmatch(
        r'Remove (?P<target>target attacking or blocking creature|target attacking creature you control) '
        r'from combat(?P<untap> and untap it)?\.?', text.strip(), re.I,
    )
    if match is None or bool(match['untap']) != (match['target'].casefold() == 'target attacking creature you control'):
        return None
    target = direct_permanent_target_spec(match['target'])
    if target is None:
        return None
    instruction = {'op': COMBAT_REMOVAL_OPERATION, 'card': '$target.0'}
    if match['untap']:
        instruction['untap'] = 'after'
    return ('fixed-target-combat-removal-v1', (instruction,), target.to_target_schema(),
            (COMBAT_REMOVAL_MECHANIC, 'cr-115-targets'))


def optional_blocked_combat_removal_template(binding, body):
    if binding.event.value != 'creature.becomes_blocked':
        return None
    subjects = {
        'this_creature_becomes_blocked': {'it', 'this creature'},
        'a_creature_you_control_becomes_blocked': {'that creature'},
    }.get(binding.variant, ())
    match = re.fullmatch(r'You may untap (?P<subject>it|this creature|that creature) and remove it from combat\.', body, re.I)
    if match is None or match['subject'].casefold() not in subjects:
        return None
    instruction = {'op': COMBAT_REMOVAL_OPERATION, 'card': BLOCKED_ATTACKER_REFERENCE, 'untap': 'before'}
    optional = fixed_optional_effect_template(body, compile_effect=lambda _text: (
        'fixed-blocked-attacker-combat-removal-v1', (instruction,), None, (COMBAT_REMOVAL_MECHANIC,),
    ))
    return optional.compiled() if optional is not None else None
