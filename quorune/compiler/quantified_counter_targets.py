from __future__ import annotations

"""Complete homogeneous counter results reuse the scalar counter grammar."""

from . import regex_working_set as re

from ..counter_names import existing_counter_amount_descriptor
from ..target_cardinality import AnnouncedTargetCardinality, selected_target_count_descriptor
from .counter_placement_templates import fixed_counter_placement_effect_template
from .fixed_homogeneous_target_sets import _singularize_subject, _target_bounds

QUANTIFIED_COUNTER_MECHANIC = 'quantified-counter-target-set'
_COUNT = r'any number of|up to (?:one|two|three|four|five|six)|one or two|two|three|four|five|six'
_CHOOSE = re.compile(rf'Choose (?P<count>{_COUNT}) target (?P<subject>[^.]+)\. (?P<result>(?:Put|Double) .+?) on each of (?:them|those creatures)\.',re.I)
_INLINE = re.compile(rf'(?P<result>(?:Put|Double) .+?) on (?:each of )?(?P<count>{_COUNT}) target (?P<subject>[^.]+)\.',re.I)


def _split_two_counter_template(text):
    match = re.fullmatch(
        r"Distribute two (?P<counter>[+]1/[+]1|-1/-1) counters among one or two target creatures"
        r"(?P<controlled> you control)?[.]", text.strip(), re.I,
    )
    if match is None:
        return None
    from ..split_two_counter_target_amount import SplitTwoCounterTargetAmount

    ordinary = quantified_counter_target_template(
        f"Put a {match['counter']} counter on each of one or two target creatures{match['controlled'] or ''}."
    )
    if ordinary is None:
        return None
    _, (effect,), schema, mechanics = ordinary
    return ("split-two-counter-targets-v1", ({**effect, "amount": SplitTwoCounterTargetAmount().to_dict()},),
            schema, mechanics)


def quantified_counter_target_template(text):
    if split := _split_two_counter_template(text):
        return split
    match=_CHOOSE.fullmatch(text.strip()) or _INLINE.fullmatch(text.strip())
    if match is None:return None
    result=match['result'];doubling=result.casefold().startswith('double the number of ')
    if doubling:
        counter=re.fullmatch(r'Double the number of (?P<name>.+) counters',result,re.I)
        if counter is None or counter['name'].casefold() in {'each kind of','each type of','all','any','those','the chosen'}:return None
        result='Put a '+counter['name']+' counter'
    subject=_singularize_subject(match['subject'])
    base=fixed_counter_placement_effect_template(result+' on target '+subject+'.',card_name='Counter source')
    if base is None:return None
    compiled=base.compiled();effect=dict(compiled[1][0]);schema=dict(compiled[2] or {})
    if effect.pop('op',None)!='place_counters' or effect.pop('card',None)!='$target.0' or schema.pop('count',None)!=1:return None
    count=match['count'].casefold()
    variable=count=='any number of'
    if variable:
        schema['count_parameter']=AnnouncedTargetCardinality(selection='up_to',parameter='any_number',schema_version=2).to_dict()
        maximum=selected_target_count_descriptor()
    else:
        minimum,maximum,_=_target_bounds(count)
        if minimum==maximum:schema['count']=maximum
        elif minimum==0:schema['up_to']=maximum
        else:schema.update(min=minimum,max=maximum)
    if doubling:effect['amount']=existing_counter_amount_descriptor()
    effect.update(op='place_counters_on_targets',schema_version=2,cards='$targets',maximum_targets=maximum)
    if variable:effect['announced_target_count']=True
    mechanics=tuple(dict.fromkeys((*compiled[3],QUANTIFIED_COUNTER_MECHANIC,
        *(('named-counter-doubling',) if doubling else ()),*(('any-number-target-set',) if variable else ()))))
    return ('quantified-named-counter-target-set-v2',(effect,),schema,mechanics)
