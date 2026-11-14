"""Bounded printed-order composition of independent public conditional leaves."""
from .closed_effect_programs import ClosedEffectProgramTemplate,_sentence_partition,CLOSED_EFFECT_PROGRAM_MECHANIC
from .resolution_condition_templates import resolution_condition_template

SEQUENTIAL_CONDITION_MECHANIC='sequential-public-conditions'
SEQUENTIAL_CONDITION_CAPABILITY='resolution.effect_program.sequential_public_conditions'
SEQUENTIAL_CONDITION_TEMPLATE='sequential-public-condition-program-v1'


class SequentialPublicConditionTemplate(ClosedEffectProgramTemplate):
    @property
    def template_id(self):return SEQUENTIAL_CONDITION_TEMPLATE


def sequential_public_condition_template(text,*,source_name,compile_component):
    clauses=_sentence_partition(text)
    if clauses is None or not 2<=len(clauses)<=4:return None
    compiled=tuple(resolution_condition_template(clause,source_name=source_name,compile_component=compile_component) for clause in clauses)
    if any(c is None or c[2] is not None or len(c[1])!=1 for c in compiled):return None
    effects=tuple(c[1][0] for c in compiled)
    if any(e.get('prefix_mechanic_ids') or e.get('schema_version') is not None for e in effects):return None
    mechanics=tuple(dict.fromkeys((CLOSED_EFFECT_PROGRAM_MECHANIC,SEQUENTIAL_CONDITION_MECHANIC,*(m for c in compiled for m in c[3]))))
    return SequentialPublicConditionTemplate(tuple(c[0] for c in compiled),tuple(1 for c in compiled),effects,None,mechanics).compiled()
