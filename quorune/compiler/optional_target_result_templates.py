"""Optional single removal followed by a same-target characteristic result."""
from copy import deepcopy
from dataclasses import dataclass
from . import regex_working_set as re
from ..optional_target_result_model import (
    OPTIONAL_TARGET_RESULT_FIELD, OPTIONAL_TARGET_RESULT_MECHANIC,
    OPTIONAL_TARGET_RESULT_TEMPLATE, OptionalTargetResultSpec,
)
from .fixed_homogeneous_target_sets import FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC

_GRAMMAR = re.compile(r"Exile up to one (?P<other>other )?target creature\. "
    r"(?P<tail>(?:Its controller|That creature['’]s controller) gains life equal to its power\.)",re.I)


@dataclass(frozen=True, slots=True)
class OptionalTargetResultTemplate:
    effects: tuple
    target_schema: dict
    mechanics: tuple

    def compiled(self):
        return OPTIONAL_TARGET_RESULT_TEMPLATE,deepcopy(self.effects),deepcopy(self.target_schema),self.mechanics


def optional_target_result_template(text, *, compile_mandatory):
    match=_GRAMMAR.fullmatch(" ".join(text.strip().split()))
    if match is None:return None
    prefix="Exile another target creature. " if match['other'] else "Exile target creature. "
    name,effects,schema,mechanics=compile_mandatory(prefix+match['tail'])
    if name is None or len(effects)!=2 or schema is None or schema.get('count')!=1:return None
    from ..rules.optional_target_results import optional_target_result_payload_dependencies
    result={**dict(effects[1]),OPTIONAL_TARGET_RESULT_FIELD:OptionalTargetResultSpec().to_dict()}
    try:optional_target_result_payload_dependencies(result)
    except (TypeError,ValueError):return None
    if effects[0]!={'op':'exile_permanent','card':'$target.0'}:return None
    optional_schema={k:v for k,v in schema.items() if k!='count'}
    optional_schema['up_to']=1
    return OptionalTargetResultTemplate(
        ({'op':'exile_permanent_targets','cards':'$targets','maximum_targets':1},result),
        optional_schema,tuple(dict.fromkeys((*mechanics,FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC,
            OPTIONAL_TARGET_RESULT_MECHANIC))))
