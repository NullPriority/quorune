"""Compile one printed Mutate cost without promoting incomplete gameplay."""
import re
from ..ability_fragments import ability_fragment_to_dict,CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..mutate_model import MutateSpec,MUTATE_CAPABILITY,MUTATE_HANDLER
from .static_runtime_nodes import runtime_handler_node


def mutate_keyword_node(**values):
    if values['mechanics']!=('mutate',):return None
    match=re.fullmatch(r'Mutate (?P<cost>(?:\{[^{}]+\})+)\.?',values['material_line'].strip(),re.I)
    if match is None:return None
    try:spec=MutateSpec(match['cost'].upper())
    except (TypeError,ValueError):return None
    return runtime_handler_node(node_id=values['node_id'],line=values['line'],span=values['span'],
        compiled=('fixed-mana-mutate-cost-v1',{'handler_id':MUTATE_HANDLER,'schema_version':1,
            'event':'characteristics.evaluate','fragment':ability_fragment_to_dict(spec),
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD:True},MUTATE_CAPABILITY),kind='static_ability',
        event='characteristics.evaluate',active_zone='all',runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Mutate requires complete alternative casting, same-spell-owner targeting, fallback resolution, merge and event authority.',
        capability_registry=values['capability_registry'],capability_profile=values['capability_profile'],residuals=values['residuals'])
