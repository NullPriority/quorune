"""Bind a complete attacker-pronoun characteristic instruction to its occurrence."""

from . import regex_working_set as re
from dataclasses import replace

from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
from ..qualified_attack_model import QUALIFIED_ATTACK_VARIANT
from ..query_effect_amount_model import (
    PUBLIC_QUERY_AMOUNT_KIND, PublicQueryAmountSpec, attack_shared_type_amount_spec,
)
from .fixed_target_effect_sequences import FixedSourceCharacteristicsTemplate
from .public_event_closure_variants import ATTACHED_CREATURE_ATTACK_VARIANTS


_OTHER_CONTROLLED_CREATURES = re.compile(
    r"(?P<result>gets [+-]\d+/[+-]\d+ until end of turn) for each other creature you control\.?", re.I,
)


_FIXED_KEYWORD_GRANT = re.compile(r"gains (?P<keyword>[a-z ]+) until end of turn\.", re.I)


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

    if (
        binding.variant == "public_attack_a_creature_you_control"
        and " ".join(body.casefold().split()) ==
            "it gets +1/+0 until end of turn for each other attacking creature that shares a creature type with it."
    ):
        from .public_query_effect_amounts import PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC

        base = FixedSourceCharacteristicsTemplate("attack-occurrence", power=1, toughness=0).compiled()
        effect = {**base[1][0], "card": "$event.permanent_object", "power": attack_shared_type_amount_spec().to_dict()}
        mechanics = (*base[3], PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC)
        return ("fixed-attack-shared-type-power-v1", (effect,), None, mechanics), True

    multiplier = attack_occurrence_multiplier_template(body)
    if multiplier is not None and attack_event_characteristic_capabilities(
        effects=multiplier[1], target_schema=None, mechanic_ids=multiplier[3],
    ):
        return multiplier, True
    match=re.fullmatch(r'(?:it|that creature|that permanent) (?P<body>.+)',body,re.I)
    if match is None:return None
    other = _OTHER_CONTROLLED_CREATURES.fullmatch(match['body'])
    material = f"{other['result']} for each creature you control." if other is not None else match['body']
    compiled = effect_template('This permanent '+material, card_name=card_name)
    if not (len(compiled[1]) == 1 and compiled[1][0].get('op') == 'apply_source_characteristics_until_end_of_turn'):
        grant = _FIXED_KEYWORD_GRANT.fullmatch(material)
        keyword = grant['keyword'].title() if grant is not None else None
        if keyword in FIXED_CHARACTERISTIC_KEYWORDS:
            compiled = FixedSourceCharacteristicsTemplate(
                source_kind='attack-occurrence', keywords=(keyword,),
            ).compiled()
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
