from __future__ import annotations

"""Complete fixed resolution-payment clauses over existing typed result leaves."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import re
from typing import Any, Callable, Mapping

from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC, FixedEffectPaymentSpec
from ..replacement.immutable import FrozenMap
from ..util import mana_cost_to_vector, stable_json
from .optional_payment_templates import OPTIONAL_MANA_PAYMENT_OPERATION
from .spell_additional_cost_templates import (
    fixed_sacrifice_additional_cost_template, fixed_zone_change_additional_cost_template,
)


_PAYMENT = re.compile(r"You may (?P<cost>.+?)\. If you do, (?P<body>.+)", re.I)
_OPEN = re.compile(r"\b(?:if|unless|may|when|where|random|repeat|this way|that much|equal to|for each)\b", re.I)
Compiled = tuple[str | None, tuple[Mapping[str, Any], ...], Mapping[str, Any] | None, tuple[str, ...]]


def fixed_effect_payment_spec(text: str) -> FixedEffectPaymentSpec | None:
    value=text.strip()
    compound=re.fullmatch(r'pay (?P<mana>(?:\{(?:[0-9]+|[WUBRGC])\})+) and discard a card',value,re.I)
    if compound is not None:
        from ..object_predicate import ObjectQuerySpec
        requirements,complex_symbols=mana_cost_to_vector(compound['mana'])
        if complex_symbols or not any(requirements.values()):return None
        return FixedEffectPaymentSpec('mana_discard',schema_version=5,requirements=FrozenMap(requirements),
            predicate=ObjectQuerySpec(zones=('hand',),owner='$controller'))
    exile = _owned_graveyard_exile_payment(value)
    if exile is not None:return exile
    tap = _fixed_untapped_permanent_payment(value)
    if tap is not None:return tap
    counter = _fixed_source_counter_payment(value)
    if counter is not None:return counter
    energy=re.fullmatch(r'pay (?P<symbols>(?:\{E\})+)',value,re.I)
    if energy is not None:return FixedEffectPaymentSpec('energy',amount=len(energy['symbols'])//3)
    mana=re.fullmatch(r"pay (?P<cost>(?:\{(?:[0-9]+|[WUBRGC])\})+)",value,re.I)
    if mana is not None:
        requirements,symbols=mana_cost_to_vector(mana['cost'])
        if symbols or not any(requirements.values()):return None
        return FixedEffectPaymentSpec('mana',requirements=FrozenMap(requirements))
    life=re.fullmatch(r"pay (?P<amount>[1-9][0-9]*) life",value,re.I)
    if life is not None:return FixedEffectPaymentSpec('life',amount=int(life['amount']))
    clause='As an additional cost to cast this spell, '+value+'.'
    if value.casefold().startswith('discard '):
        if value.casefold()=='discard two cards':
            from ..object_predicate import ObjectQuerySpec
            return FixedEffectPaymentSpec('discard',amount=2,predicate=ObjectQuerySpec(zones=('hand',),owner='$controller',known_to_actor=True))
        parsed=fixed_zone_change_additional_cost_template(clause)
        if parsed is None or parsed.operation!='discard_one':return None
        return FixedEffectPaymentSpec('discard',predicate=replace(parsed.predicate,owner='$controller'))
    if value.casefold().startswith('sacrifice '):
        parsed=fixed_sacrifice_additional_cost_template(clause)
        from ..object_predicate import ObjectQuerySpec
        if parsed is not None:predicate=ObjectQuerySpec.from_dict(parsed.descriptor['predicate'])
        else:
            zone_cost=fixed_zone_change_additional_cost_template(clause)
            if zone_cost is None or zone_cost.operation!='sacrifice_one':return None
            predicate=zone_cost.predicate
        return FixedEffectPaymentSpec('sacrifice',predicate=replace(predicate,controller='$controller'))
    return None


def _fixed_source_counter_payment(value):
    from ..abilities import _source_counter_removal_cost
    from ..rules.source_references import source_self_permanent_type
    if source_self_permanent_type(value.rsplit(' from ',1)[-1]) is None:return None
    parsed=_source_counter_removal_cost(value,card_name='source')
    if parsed is None:return None
    return FixedEffectPaymentSpec('remove_source_counter',schema_version=4,amount=parsed.amount,
        counter_name=parsed.counter_name,source_ref='$source.zone_object')


def _fixed_untapped_permanent_payment(value):
    from .activated_tap_costs import fixed_tap_activation_cost_choice
    from ..object_predicate import ObjectQuerySpec
    normalized=value.casefold()
    surface,nonhuman=re.subn(r'\bnon-human (?=creatures?\b)','',normalized,count=1)
    choice=fixed_tap_activation_cost_choice(surface)
    if choice is None:return None
    predicate=ObjectQuerySpec.from_dict(choice.predicate)
    if nonhuman and predicate.types_all!=('creature',):return None
    predicate=replace(predicate,controller='$controller',
        exclude_ref='$source.zone_object' if choice.another else None,
        excluded_subtypes=('human',) if nonhuman else ())
    try:return FixedEffectPaymentSpec('tap',amount=choice.count,predicate=predicate,schema_version=3)
    except (TypeError,ValueError):return None


def _owned_graveyard_exile_payment(value):
    match=re.fullmatch(r'exile (?P<count>a|an|one|two|three|four|five|six|seven|eight|nine|ten|[1-9][0-9]*) (?P<cards>.+?) from your graveyard',value,re.I)
    if match is None:return None
    from .fixed_numbers import fixed_number
    from .cast_cost_modifier_templates import fixed_spell_predicate
    from ..object_predicate import ObjectQuerySpec
    amount=1 if match['count'].casefold() in {'a','an'} else fixed_number(match['count'])
    noun='card' if amount==1 else 'cards'
    cards=match['cards'].casefold()
    if cards==noun:predicate=ObjectQuerySpec()
    elif cards.endswith(' '+noun):predicate=fixed_spell_predicate(cards[:-len(noun)].strip()+' spells you cast')
    else:return None
    if predicate is None:return None
    try:
        return FixedEffectPaymentSpec('exile',amount=amount,schema_version=3,
            predicate=replace(predicate,zones=('graveyard',),owner='$controller',token=False,known_to_actor=True))
    except (TypeError,ValueError):return None


def fixed_effect_payment_template(text: str, *, compile_effect: Callable[[str], Compiled]) -> Compiled | None:
    match=_PAYMENT.fullmatch(text.strip())
    if match is None:return None
    cost=fixed_effect_payment_spec(match['cost'])
    body=match['body'].strip()
    if cost is None or _OPEN.search(body) or '"' in body:return None
    template,effects,schema,mechanics=compile_effect(body)
    if len(effects)==1 and effects[0].get('op')=='modify_stats_until_end_of_turn' and effects[0].get('card')=='$source':
        from .fixed_target_effect_sequences import FixedSourceCharacteristicsTemplate
        source_result=FixedSourceCharacteristicsTemplate(source_kind='permanent',power=effects[0]['power'],toughness=effects[0]['toughness'])
        template,effects,schema,mechanics=source_result.compiled()
    if template is None or not 1<=len(effects)<=4 or not mechanics or any(
        effect.get('op') in {'offer_optional_effect',OPTIONAL_MANA_PAYMENT_OPERATION} for effect in effects
    ) or FIXED_EFFECT_PAYMENT_MECHANIC in mechanics:return None
    from ..rules.fixed_effect_payment_shapes import fixed_payment_result_capabilities
    if not fixed_payment_result_capabilities(effects=effects,target_schema=schema,mechanic_ids=mechanics):
        return None
    value={'op':OPTIONAL_MANA_PAYMENT_OPERATION,'schema_version':2,'player':'$controller',
           'payment':cost.to_dict(),'effects':deepcopy(list(effects))}
    if cost.kind=='mana':value['cost']=dict(cost.requirements)
    identity=hashlib.sha256(stable_json(value).encode('utf-8')).hexdigest()[:16]
    return ('fixed-resolution-payment-'+identity+'-v2',(value,),schema,
            tuple(dict.fromkeys((FIXED_EFFECT_PAYMENT_MECHANIC,*mechanics))))


def fixed_effect_payment_with_mandatory_prefix(text: str, *, compile_effect: Callable[[str], Compiled]) -> Compiled | None:
    """One independently typed mandatory clause stays outside payment scope."""
    match=re.fullmatch(r"(?P<first>.+?\.) (?:Then )?(?P<payment>You may .+)",text.strip(),re.I)
    if match is None or _OPEN.search(match['first']):return None
    first=compile_effect(match['first'])
    paid=fixed_effect_payment_template(match['payment'],compile_effect=compile_effect)
    if first[0] is None or len(first[1])!=1 or paid is None or first[2] is not None and paid[2] is not None:return None
    return ('fixed-payment-mandatory-prefix-v2',(*first[1],*paid[1]),first[2]or paid[2],
            tuple(dict.fromkeys(('fixed-effect-clause-sequence',*first[3],*paid[3]))))


def is_closed_fixed_effect_payment_program(program: Any) -> bool:
    from ..rules.fixed_effect_clause_shapes import fixed_optional_mana_payment_node_capabilities
    required=set(fixed_optional_mana_payment_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage))
    return bool(required) and required <= set(program.capability_dependencies)
