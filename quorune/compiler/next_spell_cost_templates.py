"""Full-consumption next-spell reductions over existing public spell queries."""
from __future__ import annotations
from . import regex_working_set as re

from ..next_spell_cost_model import (
    OPERATION, MECHANIC, recurring_spell_cost_query, validate_next_spell_cost_instruction,
)
from ..query_effect_amount_model import PublicQueryAmountSpec
from ..scalar_effect_amount_model import ScalarAmountOrigin,ScalarEffectAmountSpec
from .cast_cost_modifier_templates import fixed_spell_predicate
from .query_characteristic_templates import query_characteristic_value
from ..rules.source_references import SourceReferenceSpec


def next_spell_cost_reduction_template(text,*,source_name,source_is_permanent=None):
    if " ".join(text.casefold().strip().removesuffix(".").split()) == (
        "the next red or green creature spell you cast this turn can be cast without paying its mana cost"
    ):
        from ..next_spell_cost_model import NEXT_ELIGIBLE_FREE_MECHANIC, next_eligible_free_spell_effect

        return ("next-eligible-red-or-green-creature-mana-alternative-v1", (next_eligible_free_spell_effect(),),
                None, (MECHANIC, NEXT_ELIGIBLE_FREE_MECHANIC))
    match=re.fullmatch(r'The next (?P<quality>.*?)spell you cast this turn costs \{(?P<amount>[1-9]\d*|X)\} less to cast'
        r'(?:, where X is (?P<value>.+?))?\.', ' '.join(text.strip().split()),re.I)
    if match is None or (match['amount'].upper()=='X')!=(match['value'] is not None):return None
    query=fixed_spell_predicate((match['quality'].strip()+' spells you cast').strip())
    if query is None:return None
    mechanics=[MECHANIC]
    if match['value'] is None:amount=int(match['amount'])
    else:
        value=re.sub(r' as this ability resolves$','',match['value'],flags=re.I)
        quantity=query_characteristic_value(value,source_name=source_name)
        try:amount=PublicQueryAmountSpec(quantity).to_dict() if quantity is not None else None
        except ValueError:return None
        if amount is not None:mechanics.append('public-query-effect-amount')
        else:
            stat=re.fullmatch(r"(?P<source>.+?)['’]s (?P<stat>power|toughness)",value,re.I)
            if (stat is None or source_is_permanent is not True or not (
                    re.fullmatch(r'this (?:creature|artifact|permanent)',stat['source'],re.I)
                    or SourceReferenceSpec(source_name).matches(stat['source']))):return None
            amount=ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE,characteristic=stat['stat'].casefold()).to_dict()
            mechanics.append('scalar-effect-amount')
    effect={'op':OPERATION,'schema_version':1,'spell_query':query.to_dict(),'amount':amount}
    try:validate_next_spell_cost_instruction(effect)
    except (ValueError,TypeError):return None
    return 'next-eligible-spell-cost-reduction-v1',(effect,),None,tuple(mechanics)


def recurring_attack_spell_cost_template(binding, body):
    if (
        (binding.event.value, binding.variant, binding.active_zone)
        != ("creature.attacks", "source_attacks", "battlefield")
        or binding.event_condition != {"field": "card", "op": "eq", "value": "$source.ref"}
        or body != binding.body
    ):
        return None
    matched = re.fullmatch(
        r"(?P<scope>Instant and sorcery|Face-down) spells you cast this turn cost \{1\} less to cast[.]?",
        body, re.I,
    )
    if matched is None:
        return None
    face_down_only = matched["scope"].casefold() == "face-down"
    effect = {
        "op": OPERATION, "schema_version": 2, "amount": 1, "face_down_only": face_down_only,
        "spell_query": recurring_spell_cost_query(face_down_only).to_dict(),
    }
    return "attack-recurring-spell-cost-reduction-v2", (effect,), None, (MECHANIC,)
