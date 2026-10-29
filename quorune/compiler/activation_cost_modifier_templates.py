from __future__ import annotations

"""Closed flat generic activation-cost modifiers over existing public queries."""

import re
from dataclasses import replace

from ..activation_cost_modifiers import (
    ACTIVATION_GENERIC_COST_CAPABILITY, ACTIVATION_GENERIC_COST_EVENT,
    ACTIVATION_GENERIC_COST_HANDLER, FixedActivationCostModifierSpec,
)
from ..object_predicate import ObjectQuerySpec
from .public_state_queries import fixed_battlefield_query_subject


_COST = re.compile(r"(?P<head>.+?) costs? \{(?P<amount>[1-9][0-9]*)\} "
    r"(?P<direction>more|less) to activate(?P<nonmana> unless they're mana abilities)?\.?"
    r"(?P<minimum> This effect can't reduce the (?:amount of )?mana in "
    r"(?:that|this) (?:ability's activation )?cost to less than one mana\.)?", re.I)


def _modifier_subject(head):
    lowered = head.casefold()
    if lowered in {'equip abilities you activate','cycling abilities you activate'}:
        return 'any','source_controller',lowered.split()[0],ObjectQuerySpec()
    if lowered == 'activated abilities':
        return 'any', 'any', 'all', ObjectQuerySpec()
    if lowered == "abilities you activate that aren't mana abilities":
        return 'any', 'source_controller', 'nonmana', ObjectQuerySpec()
    attached = re.fullmatch(r"enchanted (?P<kind>artifact|creature|land|enchantment|permanent)'s activated abilities", lowered)
    if attached is not None:
        kind = attached['kind']
        return 'enchanted_object', 'any', 'all', ObjectQuerySpec(zones=('battlefield',),
            types_all=() if kind == 'permanent' else (kind,))
    if not lowered.startswith('activated abilities of '):
        return None
    subject = head[len('activated abilities of '):]
    parsed = fixed_battlefield_query_subject(subject)
    if parsed is None:
        colored = re.fullmatch(r'(white|blue|black|red|green) (.+)', subject, re.I)
        if colored is not None:
            base = fixed_battlefield_query_subject(colored[2])
            if base is not None and not base[1].colors_all and not base[1].colors_any:
                symbols = {'white': 'W', 'blue': 'U', 'black': 'B', 'red': 'R', 'green': 'G'}
                parsed = (base[0], replace(base[1], colors_all=(symbols[colored[1].casefold()],)), base[2])
    if parsed is None and subject.casefold() == 'foods you control':
        parsed = ('source_controller', ObjectQuerySpec(zones=('battlefield',), subtypes_all=('food',)), False)
    if parsed is None:
        return None
    relation, predicate, exclude_source = parsed
    if exclude_source or relation not in {'any', 'source_controller', 'source_opponents'}:
        return None
    return 'query', relation, 'all', predicate


def static_activation_cost_modifier_handler(text):
    material=text.strip().replace('’', "'")
    legacy_equip=re.fullmatch(r'Equip costs you pay cost (?P<amount>\{[1-9][0-9]*\}) less\.',material,re.I)
    if legacy_equip is not None:material=f"Equip abilities you activate cost {legacy_equip['amount']} less to activate."
    match = _COST.fullmatch(material)
    if match is None:
        return None
    subject = _modifier_subject(match['head'])
    if subject is None:
        return None
    relation, controller, scope, predicate = subject
    if match['nonmana']:
        scope = 'nonmana'
    amount = int(match['amount']) * (1 if match['direction'].casefold() == 'more' else -1)
    try:
        spec = FixedActivationCostModifierSpec(amount, predicate=predicate,
            subject_relation=relation, controller_relation=controller, ability_scope=scope,
            minimum_mana=1 if match['minimum'] else 0)
    except ValueError:
        return None
    descriptor = {'handler_id': ACTIVATION_GENERIC_COST_HANDLER, 'schema_version': 1,
        'event': ACTIVATION_GENERIC_COST_EVENT, 'modifier': spec.to_dict()}
    return 'fixed-public-activation-cost-modifier-v1', descriptor, ACTIVATION_GENERIC_COST_CAPABILITY


def activation_cost_modifier_handler(text, *, source_name):
    from .targeted_activation_costs import targeted_activation_cost_template
    from .public_quantity_activation_costs import public_quantity_activation_cost_template
    return (targeted_activation_cost_template(text, source_name=source_name)
        or public_quantity_activation_cost_template(text, source_name=source_name)
        or static_activation_cost_modifier_handler(text))
