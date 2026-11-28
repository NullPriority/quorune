"""Optional single removal followed by a same-target result."""
from copy import deepcopy
from dataclasses import dataclass
from . import regex_working_set as re
from ..optional_target_result_model import (
    OPTIONAL_TARGET_RESULT_FIELD, OPTIONAL_TARGET_RESULT_MECHANIC,
    OPTIONAL_LAND_CONTROLLER_SEARCH_TEMPLATE, OPTIONAL_TARGET_RESULT_TEMPLATE, OptionalTargetResultSpec,
)
from .fixed_homogeneous_target_sets import FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC

_GRAMMAR = re.compile(r"Exile up to one (?P<other>other )?target creature\. "
    r"(?P<tail>(?:Its controller|That creature['’]s controller) gains life equal to its power\.)",re.I)
_LAND_SEARCH_GRAMMAR = re.compile(
    r"Destroy up to one target nonbasic land\. "
    r"(?P<tail>(?:Its controller|That land['’]s controller) may search their library for a basic land card, "
    r"put it onto the battlefield tapped, then shuffle\.)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class OptionalTargetResultTemplate:
    effects: tuple
    target_schema: dict
    mechanics: tuple
    template_id: str = OPTIONAL_TARGET_RESULT_TEMPLATE

    def compiled(self):
        return self.template_id, deepcopy(self.effects), deepcopy(self.target_schema), self.mechanics


def _optional_nonbasic_land_search(match, compile_mandatory):
    from .bound_effect_programs import BOUND_EFFECT_PROGRAM_MECHANIC, CLOSED_EFFECT_PROGRAM_MECHANIC
    from .fixed_homogeneous_target_sets import fixed_homogeneous_target_set_effect_template
    from ..rules.optional_target_results import optional_target_result_node_capabilities

    name, effects, schema, mechanics = compile_mandatory("Destroy target nonbasic land. " + match["tail"])
    if name is None or len(effects) != 2 or schema is None or schema.get("count") != 1:
        return None
    batch = fixed_homogeneous_target_set_effect_template("Destroy up to one target nonbasic land.")
    if batch is None:
        return None
    _, (destruction,), target_schema, batch_mechanics = batch.compiled()
    search = {**effects[1], OPTIONAL_TARGET_RESULT_FIELD: OptionalTargetResultSpec().to_dict()}
    ordered = (destruction, search)
    mandatory_markers = {BOUND_EFFECT_PROGRAM_MECHANIC, CLOSED_EFFECT_PROGRAM_MECHANIC}
    component_mechanics = tuple(marker for marker in mechanics if marker not in mandatory_markers)
    coverage = tuple(dict.fromkeys((*component_mechanics, *batch_mechanics, OPTIONAL_TARGET_RESULT_MECHANIC)))
    if not optional_target_result_node_capabilities(effects=ordered, target_schema=target_schema, mechanic_ids=coverage):
        return None
    return OptionalTargetResultTemplate(ordered, target_schema, coverage, OPTIONAL_LAND_CONTROLLER_SEARCH_TEMPLATE)


def optional_target_result_template(text, *, compile_mandatory):
    normalized = " ".join(text.split())
    if search := _LAND_SEARCH_GRAMMAR.fullmatch(normalized):
        return _optional_nonbasic_land_search(search, compile_mandatory)
    match = _GRAMMAR.fullmatch(normalized)
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
