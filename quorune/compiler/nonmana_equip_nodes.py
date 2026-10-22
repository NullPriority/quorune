from __future__ import annotations

"""Fixed nonmana Equip uses the existing typed activation and attachment owners."""

from ..abilities import parse_activated_abilities
from ..rules.attachment_actions import FIXED_NONMANA_EQUIP_CAPABILITY, fixed_equip_ability_spec
from ..semantic_runtime.activated_abilities import activated_ability_catalog_descriptor
from .activated_ability_catalog import specialize_compiled_activation_costs
from .activated_costs import activated_ability_cost, activated_ability_cost_capabilities
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


def is_closed_nonmana_equip_program(program):
    from dataclasses import replace
    from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors
    from ..replacement.immutable import thaw_value
    from .oracle_source_text import without_parenthetical_reminder
    if FIXED_NONMANA_EQUIP_CAPABILITY not in program.capability_dependencies:
        return False
    if program.event != 'activate' or program.active_zone != 'battlefield':
        return False
    if len(program.handlers) != 1 or program.handlers[0].get('requires_complete_card_program') is not True:
        return False
    abilities = activated_abilities_from_descriptors(program.handlers)
    if len(abilities) != 1:
        return False
    ability = abilities[0]
    spec = fixed_equip_ability_spec(without_parenthetical_reminder(ability.oracle_line))
    if spec is None or not spec.nonmana:
        return False
    parsed = parse_activated_abilities(card_name='source', oracle_text=ability.oracle_line, keywords=('Equip',))
    canonical = specialize_compiled_activation_costs(parsed, source_name='source')
    if len(canonical) != 1:
        return False
    expected = replace(canonical[0], ability_id=ability.ability_id, line_index=ability.line_index)
    if ability != expected or not ability.compiled_cost:
        return False
    required = {FIXED_NONMANA_EQUIP_CAPABILITY, 'attachment.action.fixed_source',
        'target.revalidate_resolution', *activated_ability_cost_capabilities(ability)}
    return (required <= set(program.capability_dependencies)
        and thaw_value(program.cost_schema) == activated_ability_cost(ability)
        and thaw_value(program.target_schema) == dict(spec.target_schema)
        and list(program.effects) == [{'op':'attach','equipment':'$source.zone_object',
            'creature':'$target.0','reason':'Equip'}])


def fixed_nonmana_equip_keyword_node(*, record, node_id, line, material_line, span,
    mechanics, capability_registry, capability_profile, residuals):
    spec = fixed_equip_ability_spec(material_line)
    if mechanics != ('equip',) or spec is None or not spec.nonmana:
        return None
    parsed = parse_activated_abilities(card_name=record.name, oracle_text=line, keywords=('Equip',))
    abilities = specialize_compiled_activation_costs(parsed, source_name=record.name)
    if len(abilities) != 1 or not abilities[0].compiled_cost:
        return None
    from dataclasses import replace
    ability = replace(abilities[0], ability_id=f'ab{span.line}', line_index=span.line-1)
    gate = explicit_capabilities_gate((FIXED_NONMANA_EQUIP_CAPABILITY,
        'attachment.action.fixed_source', 'target.revalidate_resolution',
        *activated_ability_cost_capabilities(ability)), capability_registry=capability_registry,
        capability_profile=capability_profile)
    ids = (append_residual(residuals, kind='dependency_contract', text=line, span=span,
        reason='Fixed nonmana Equip requires its complete cost and attachment closure',
        blockers=gate.blockers),) if gate.blockers else ()
    return OracleNode(node_id=node_id, kind='activated_ability', text=line, span=span,
        active_zone='battlefield', event='activate', lowerable=True, exact=not ids,
        template_id='fixed-nonmana-equip-activation-v1', cost=activated_ability_cost(ability),
        effects=({'op':'attach','equipment':'$source.zone_object','creature':'$target.0','reason':'Equip'},),
        handlers=(activated_ability_catalog_descriptor(ability, requires_complete_card_program=True),),
        target_schema=dict(spec.target_schema), mechanics=mechanics, residual_ids=ids,
        capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure is not None else (),
        capability_profile=gate.closure.profile if gate.closure is not None else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure is not None else None)
