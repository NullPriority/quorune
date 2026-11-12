"""Bind a complete attacker-pronoun characteristic instruction to its occurrence."""

import re
from dataclasses import replace

from ..qualified_attack_model import QUALIFIED_ATTACK_VARIANT
from ..query_effect_amount_model import PUBLIC_QUERY_AMOUNT_KIND, PublicQueryAmountSpec
from .public_event_closure_variants import ATTACHED_CREATURE_ATTACK_VARIANTS


_OTHER_CONTROLLED_CREATURES = re.compile(
    r"(?P<result>gets [+-]\d+/[+-]\d+ until end of turn) for each other creature you control\.?", re.I,
)


def _seal_attack_relative_amounts(effect):
    sealed = False
    for field in ("power", "toughness"):
        raw = effect.get(field)
        if isinstance(raw, dict) and raw.get("kind") == PUBLIC_QUERY_AMOUNT_KIND:
            spec = PublicQueryAmountSpec.from_dict(raw)
            effect[field] = replace(spec, schema_version=3,
                quantity=replace(spec.quantity, exclude_source=True)).to_dict()
            sealed = True
    return sealed


def qualified_attack_characteristic_result(binding,body,*,effect_template,card_name):
    if binding.event.value != 'creature.attacks' or not (
        binding.variant in {QUALIFIED_ATTACK_VARIANT, *ATTACHED_CREATURE_ATTACK_VARIANTS}
        or binding.variant.startswith('public_attack_')
    ):
        return None
    from .fixed_stat_multipliers import attack_occurrence_multiplier_template
    from ..rules.attack_event_results import attack_event_characteristic_capabilities

    multiplier = attack_occurrence_multiplier_template(body)
    if multiplier is not None and attack_event_characteristic_capabilities(
        effects=multiplier[1], target_schema=None, mechanic_ids=multiplier[3],
    ):
        return multiplier, True
    match=re.fullmatch(r'(?:it|that creature|that permanent) (?P<body>.+)',body,re.I)
    if match is None:return None
    other = _OTHER_CONTROLLED_CREATURES.fullmatch(match['body'])
    material = f"{other['result']} for each creature you control." if other is not None else match['body']
    compiled=effect_template('This permanent '+material,card_name=card_name)
    if (compiled[0] is not None and compiled[2] is None and len(compiled[1])==1
            and compiled[1][0].get('card') in {'$source','$source.zone_object'}):
        effect = {**compiled[1][0], 'card': '$event.permanent_object'}
        if other is not None:
            try:
                if not _seal_attack_relative_amounts(effect):
                    return (None, (), None, ()), False
            except (ValueError, TypeError):
                return (None, (), None, ()), False
        effects = (effect,)
        if attack_event_characteristic_capabilities(effects=effects,target_schema=None,mechanic_ids=compiled[3]):
            return ('fixed-attack-occurrence-characteristics-v1',effects,None,compiled[3]),True
    return (None,(),None,()),False
