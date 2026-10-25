from __future__ import annotations

"""Complete all-kind doubling over one existing direct permanent target set."""

import re
from .direct_target import direct_permanent_target_spec
from .fixed_homogeneous_target_sets import _singularize_subject,_target_bounds
from ..target_cardinality import AnnouncedTargetCardinality,selected_target_count_descriptor

ALL_KIND_COUNTER_MECHANIC='all-kind-counter-doubling'
_PATTERN=re.compile(r'Double the number of each (?:kind|type) of counter on (?P<count>any number of |up to (?:one|two|three|four|five|six) )?target (?P<subject>[^.]+)\.',re.I)


def all_kind_counter_doubling_template(text):
    match=_PATTERN.fullmatch(text.strip())
    if match is None:return None
    target=direct_permanent_target_spec('target '+_singularize_subject(match['subject']))
    if target is None:return None
    schema=target.to_target_schema();schema.pop('count',None)
    count=(match['count'] or '').strip().casefold();variable=count=='any number of'
    if variable:
        schema['count_parameter']=AnnouncedTargetCardinality(selection='up_to',parameter='any_number',schema_version=2).to_dict()
        maximum=selected_target_count_descriptor()
    elif count:
        _minimum,maximum,_=_target_bounds(count);schema['up_to']=maximum
    else:maximum=1;schema['count']=1
    effect={'op':'double_counters_on_targets','schema_version':1,'cards':'$targets','maximum_targets':maximum,'source':'$source'}
    if variable:effect['announced_target_count']=True
    return ('all-kind-counter-doubling-v1',(effect,),schema,('cr-122-counters','cr-115-targets',ALL_KIND_COUNTER_MECHANIC,
        *(('any-number-target-set',) if variable else ())))
