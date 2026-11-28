"""Mana-value destruction bounds reuse fixed sets and existing amount values."""
from dataclasses import replace
from . import regex_working_set as re

from ..mana_value_filter import ManaValueFilter, ManaValueComparison, cast_spell_mana_value_operand
from ..query_effect_amount_model import CastXAmountSpec
from .destruction_templates import mass_destruction_effect_template,_split_regeneration_prohibition
from .scalar_effect_amounts import _characteristic


def cast_mana_value_destruction_template(binding, text):
    if (binding.event.value != "spell.cast" or binding.active_zone != "battlefield"
            or re.fullmatch(r"Destroy all permanents with that spell['’]s mana value\.?",
                            " ".join(text.split()), re.IGNORECASE) is None):
        return None
    base = mass_destruction_effect_template("Destroy all permanents.")
    predicate = ManaValueFilter(ManaValueComparison.EQUAL, cast_spell_mana_value_operand())
    return replace(base, spec=replace(base.spec, mana_value_filter=predicate)).compiled()


def mana_value_destruction_template(text, *, source_name, cast_x_available=False):
    split=_split_regeneration_prohibition(text)
    if split is None:return None
    destruction,prohibition=split
    match=re.fullmatch(r'(?P<head>Destroy (?:all|each) .+?) with mana value (?P<amount>.+?)'
        r'(?P<bound> or less| or greater)?\.?',destruction,re.I)
    if match is None:return None
    amount=match['amount'];bound=(match['bound'] or '').casefold()
    comparison=ManaValueComparison.AT_MOST if bound==' or less' else (
        ManaValueComparison.AT_LEAST if bound==' or greater' else ManaValueComparison.EQUAL)
    for prefix,kind in (('less than or equal to ',ManaValueComparison.AT_MOST),
                        ('greater than or equal to ',ManaValueComparison.AT_LEAST),('equal to ',ManaValueComparison.EQUAL)):
        if amount.casefold().startswith(prefix):
            if bound:return None
            comparison=kind;amount=amount[len(prefix):];break
    if re.fullmatch(r'[0-9]+',amount):value=int(amount)
    elif amount.casefold()=='x' and cast_x_available:value=CastXAmountSpec()
    else:
        value=_characteristic(amount,source_name=source_name,body=destruction,schema=None,event=None,source_event=False)
        if value is None:return None
    try:predicate=ManaValueFilter(comparison,value)
    except ValueError:return None
    base_text=match['head']+'.'
    if prohibition is not None:base_text+=' '+prohibition+" can't be regenerated."
    base=mass_destruction_effect_template(base_text)
    if base is None:return None
    return replace(base,spec=replace(base.spec,mana_value_filter=predicate)).compiled()
