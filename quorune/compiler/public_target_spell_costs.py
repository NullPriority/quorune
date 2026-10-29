from __future__ import annotations

"""Qualified source-owned spell costs through shared spell/target predicates."""

import re

from ..object_predicate import object_query_capabilities
from ..targeted_spell_costs import (PUBLIC_TARGET_COST_CAPABILITY, TARGETED_SPELL_COST_CAPABILITY, TARGETED_SPELL_COST_HANDLER,
    TARGETED_SPELL_COST_EVENT, TargetedCostSubject, TargetedSpellCostSpec)
from .cast_cost_modifier_templates import fixed_spell_predicate
from .target_cost_subjects import target_cost_subject


def public_target_spell_cost_template(text, *, source_name):
    material = ' '.join(text.strip().split())
    match = re.fullmatch(r'(?P<spells>.+? spells|Spells)(?P<caster> you cast| your opponents cast)? that target '
        r'(?P<target>.+?) cost \{(?P<amount>[1-9]\d*)\} (?P<direction>more|less) to cast\.?', material, re.I)
    if match is None:
        return None
    predicate = fixed_spell_predicate(match['spells'] + ' you cast')
    subject = target_cost_subject(match['target'], source_name=source_name)
    if predicate is None or subject is None:
        return None
    relation = {None:'any',' you cast':'source_controller',' your opponents cast':'source_opponents'}[
        match['caster'].casefold() if match['caster'] else None]
    amount = int(match['amount']) * (1 if match['direction'].casefold() == 'more' else -1)
    try:
        spec = TargetedSpellCostSpec(TargetedCostSubject.PUBLIC_TARGET, amount, 4,
            caster_relation=relation, target_subject=subject, spell_predicate=predicate)
    except ValueError:
        return None
    return ('public-target-spell-cost-v4', {'handler_id':TARGETED_SPELL_COST_HANDLER,'schema_version':1,
        'event':TARGETED_SPELL_COST_EVENT,'modifier':spec.to_dict()},
        (TARGETED_SPELL_COST_CAPABILITY, PUBLIC_TARGET_COST_CAPABILITY, *object_query_capabilities(spec.to_dict())))
