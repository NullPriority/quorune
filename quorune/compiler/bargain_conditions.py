"""Complete same-target Bargain result replacement or appended keyword clauses."""
from copy import deepcopy
from . import regex_working_set as re
from ..bargain_model import BARGAIN_INSTANCE
from ..declared_cast_cost_model import DECLARED_CAST_COST_MECHANIC, DeclaredCastCostKind
from ..declared_cost_conditions import DeclaredCastCostCondition, validate_declared_cost_instruction
from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC


def bargain_same_target_template(text, *, source_name, compile_component):
    procedure = bargain_procedure_template(text, compile_component=compile_component)
    if procedure is not None:return procedure
    match = re.fullmatch(r'(?P<normal>.+?\.) If this spell was bargained, (?P<other>that creature .+)\.', text.strip(), re.I)
    if match is None:return None
    normal = compile_component(match['normal'])
    schema = normal[2]
    if normal[0] is None or schema is None or schema.get('types_any') != ['creature'] or schema.get('count') != 1:return None
    body = match['other']
    replacement = re.fullmatch(r'that creature (?P<result>gets .+) instead', body, re.I)
    appended = re.fullmatch(r'that creature also (?P<result>gains .+)', body, re.I)
    if replacement is None and appended is None:return None
    other = compile_component('Target creature ' + (replacement or appended)['result'] + '.')
    if other[0] is None or other[2] != schema:return None
    effects = other[1] if replacement else (*normal[1], *other[1])
    ids = other[3] if replacement else tuple(dict.fromkeys((*normal[3], *other[3])))
    wrapper = {'op': 'apply_if_public_condition', 'schema_version': 12, 'player': '$controller',
        'condition': DeclaredCastCostCondition(DeclaredCastCostKind.BARGAIN, BARGAIN_INSTANCE).to_dict(),
        'cast_fact': '$context.declared_cast_costs', 'effects': deepcopy(list(effects)), 'mechanic_ids': list(ids),
        'otherwise_effects': deepcopy(list(normal[1])), 'otherwise_mechanic_ids': list(normal[3])}
    try:validate_declared_cost_instruction(wrapper)
    except (ValueError, TypeError):return None
    return ('linked-bargain-same-target-v1', (wrapper,), deepcopy(schema),
        tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC, DECLARED_CAST_COST_MECHANIC, *ids, *normal[3]))))


def bargain_procedure_template(text, *, compile_component):
    from .procedure_variants import literal_procedure_variant, compiled_procedure_branches
    variant = literal_procedure_variant(text)
    if variant is None or variant.condition.casefold() != 'this spell was bargained':return None
    branches = compiled_procedure_branches(variant, compile_component)
    if branches is None:return None
    normal, alternative = branches
    wrapper = {'op': 'apply_if_public_condition', 'schema_version': 12, 'player': '$controller',
        'condition': DeclaredCastCostCondition(DeclaredCastCostKind.BARGAIN, BARGAIN_INSTANCE).to_dict(),
        'cast_fact': '$context.declared_cast_costs', 'effects': deepcopy(list(alternative[1])), 'mechanic_ids': list(alternative[3]),
        'otherwise_effects': deepcopy(list(normal[1])), 'otherwise_mechanic_ids': list(normal[3])}
    try:validate_declared_cost_instruction(wrapper)
    except (ValueError, TypeError):return None
    return ('linked-bargain-procedure-replacement-v1', (wrapper,), None,
        tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC, DECLARED_CAST_COST_MECHANIC, *normal[3], *alternative[3]))))
