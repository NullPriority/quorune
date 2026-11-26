"""Exact printed combat-removal instructions and becoming-blocked results."""

from . import regex_working_set as re

from ..combat_removal import (
    BLOCKED_ATTACKER_REFERENCE, COMBAT_REMOVAL_MECHANIC, COMBAT_REMOVAL_OPERATION,
    CONTROLLED_BLOCKED_ATTACKER, optional_blocked_attacker_return_effect,
)
from .direct_target import direct_permanent_target_spec
from .optional_effect_templates import FIXED_OPTIONAL_EFFECT_MECHANIC, fixed_optional_effect_template


def combat_removal_effect_template(text):
    text = text.strip()
    opposing = re.fullmatch(
        r'Untap (?P<target>target attacking creature an opponent controls) and remove it from combat\.?',
        text, re.I,
    )
    if opposing:
        target_text, untap = opposing['target'], 'before'
    else:
        match = re.fullmatch(
            r'Remove (?P<target>target attacking or blocking creature|target attacking creature you control) '
            r'from combat(?P<untap> and untap it)?\.?', text, re.I,
        )
        if match is None or bool(match['untap']) != (match['target'].casefold() == 'target attacking creature you control'):
            return None
        target_text, untap = match['target'], 'after' if match['untap'] else None
    target = direct_permanent_target_spec(target_text)
    if target is None:
        return None
    instruction = {'op': COMBAT_REMOVAL_OPERATION, 'card': '$target.0'}
    if untap is not None:
        instruction['untap'] = untap
    return ('fixed-target-combat-removal-v1', (instruction,), target.to_target_schema(),
            (COMBAT_REMOVAL_MECHANIC, 'cr-115-targets'))


def optional_blocked_combat_removal_template(binding, body):
    if binding.event.value != 'creature.becomes_blocked':
        return None
    if (
        binding.active_zone == 'battlefield'
        and binding.variant == 'a_creature_you_control_becomes_blocked'
        and binding.event_condition == CONTROLLED_BLOCKED_ATTACKER
        and body == binding.body
        and body.casefold() == "you may return it to its owner's hand."
    ):
        return ('optional-original-blocked-attacker-return-v1', (optional_blocked_attacker_return_effect(),),
                None, (FIXED_OPTIONAL_EFFECT_MECHANIC, 'return-to-owner-hand'))
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
