from __future__ import annotations

"""Two source-spanned abilities for the closed ordinary-mana Reconfigure form."""

import re

from ..abilities import ActivatedAbility
from ..activation_condition_model import ActivationCondition, ActivationConditionKind
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.reconfigure import RECONFIGURE_CAPABILITY
from ..semantic_runtime.activated_abilities import activated_ability_catalog_descriptor
from ..util import mana_cost_to_vector
from .activated_costs import activated_ability_cost
from .dependency_gate import explicit_capability_gate
from .ir_model import OracleNode, append_residual


def reconfigure_nodes(*, record, node_id, line, material_line, span,
        capability_registry, capability_profile, residuals):
    match = re.fullmatch(r'Reconfigure (?P<cost>(?:\{(?:\d+|[WUBRGC])\})+)\.?',
        material_line.strip())
    if match is None:
        return None
    gate = explicit_capability_gate(RECONFIGURE_CAPABILITY,
        capability_registry=capability_registry, capability_profile=capability_profile)
    residual_ids = (append_residual(residuals, kind='dependency_contract', text=line,
        span=span, reason='Reconfigure requires its activation and attachment lifetime owners',
        blockers=gate.blockers),) if gate.blockers else ()
    mana, complex_symbols = mana_cost_to_vector(match['cost'])
    schema = {'zones':['battlefield'], 'categories':['permanent'], 'controller':'you',
        'creature':True, 'count':1, 'source_exclusion':True}
    nodes = []
    for mode in ('attach', 'unattach'):
        ability_id = f'ab{span.line}-reconfigure-{mode}'
        ability = ActivatedAbility(ability_id=ability_id, line_index=span.line-1,
            oracle_line=line, cost_text=match['cost'],
            effect_text=('Attach this permanent to another target creature you control.'
                if mode == 'attach' else 'Unattach this permanent.'),
            zones=('battlefield',), mana=mana, complex_symbols=tuple(complex_symbols),
            sorcery_speed=True, reconfigure=True,
            builtin_semantic_key=f'{record.oracle_id}:ability:{ability_id}',
            target_schema=FrozenMap(schema) if mode == 'attach' else None,
            activation_conditions=() if mode == 'attach' else (
                ActivationCondition(ActivationConditionKind.SOURCE_ATTACHED_TO_CREATURE),))
        effect = ({'op':'attach', 'attachment_kind':'equipment',
            'source':'$source.zone_object', 'target':'$target.0'} if mode == 'attach'
            else {'op':'unattach', 'source':'$source.zone_object'})
        nodes.append(OracleNode(node_id=f'{node_id}:reconfigure:{mode}',
            kind='activated_ability', text=line, span=span, active_zone='battlefield',
            event='activate', lowerable=True, exact=not residual_ids,
            template_id=f'fixed-mana-reconfigure-{mode}-v1', cost=activated_ability_cost(ability),
            effects=(effect,), handlers=(activated_ability_catalog_descriptor(
                ability, requires_complete_card_program=True),),
            target_schema=schema if mode == 'attach' else None,
            mechanics=('reconfigure',), residual_ids=residual_ids,
            capability_dependencies=gate.capabilities,
            capability_closure=gate.closure.reachable if gate.closure else (),
            capability_profile=gate.closure.profile if gate.closure else None,
            capability_fingerprint=gate.closure.fingerprint if gate.closure else None))
    return tuple(nodes)


def is_closed_reconfigure_program(program):
    from ..card_programs.admission import program_has_complete_card_program_admission
    from ..rules.attachment_action_capability_shapes import reconfigure_node_capabilities
    from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors
    required = reconfigure_node_capabilities(effects=program.effects,
        target_schema=program.target_schema)
    abilities = activated_abilities_from_descriptors(program.handlers)
    if len(abilities) != 1:
        return False
    ability = abilities[0]
    conditions = (() if program.effects[0].get('op') == 'attach' else (
        ActivationCondition(ActivationConditionKind.SOURCE_ATTACHED_TO_CREATURE),)) if required else None
    return bool(required and set(required).issubset(program.capability_dependencies)
        and program.active_zone == 'battlefield' and program.event == 'activate'
        and program_has_complete_card_program_admission(program)
        and ability.reconfigure and ability.sorcery_speed
        and ability.activation_conditions == conditions
        and (thaw_value(ability.target_schema) if ability.target_schema is not None else None) == program.target_schema
        and ability.builtin_semantic_key == program.key
        and program.cost_schema == activated_ability_cost(ability))
