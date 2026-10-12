from __future__ import annotations

"""Compile a fixed-duration quoted ability through its independent child."""

from dataclasses import replace
import re

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.temporary_ability_grant import TEMPORARY_QUOTED_GRANT_CAPABILITY, TEMPORARY_QUOTED_GRANT_MECHANIC
from .attached_granted_ability_nodes import attached_granted_ability_plan
from .dependency_gate import explicit_capabilities_gate
from .direct_target import direct_permanent_target_spec
from .ir_model import OracleNode, append_residual
from .public_state_queries import fixed_characteristic_battlefield_query_subject
from .fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed


def temporary_quoted_ability_nodes(*, record, face_id, node_id, line, material_line,
        span, source_name, printed_card_types, trusted_mechanics, capability_registry,
        capability_profile, residuals, compile_inner, effect_template,
        trigger_effect_template, material_line_for):
    match = re.fullmatch(
        r'(?:Until end of turn, (?P<prefix>[^".]+?) gains? '
        r'"(?P<prefix_body>[^"\n]+)"|(?P<suffix>[^".]+?) gains? '
        r'"(?P<suffix_body>[^"\n]+)" until end of turn)\.?', material_line, re.I)
    if match is None:
        return None
    subject = match['prefix'] or match['suffix']
    quoted = match['prefix_body'] or match['suffix_body']
    if re.search(r'\b(?:enchanted|equipped|this Aura|this Equipment)\b', quoted, re.I):
        return None
    if source_name and source_name.casefold() in quoted.casefold():
        return None
    target = direct_permanent_target_spec(subject)
    query = None
    if target is None:
        parsed = fixed_characteristic_battlefield_query_subject(subject)
        if parsed is None:
            return None
        relation, query, exclude_source = parsed
        if relation == 'source_controller':
            query = replace(query, controller='$controller')
        elif relation == 'source_opponents':
            query = replace(query, excluded_controllers=('$controller',))
        elif relation != 'any':
            return None
        if exclude_source:
            query = replace(query, exclude_ref='$source')
        if query.keywords_none or not fixed_resolution_characteristic_query_is_closed(query, target_schema=None):
            return None
    inner_residuals = []
    inner = compile_inner(node_id=node_id + ':granted', line=quoted,
        material_line=material_line_for(quoted), span=span, card_name='Granted creature',
        type_line='Creature — Fixture', keywords=(), trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry, capability_profile=capability_profile,
        residuals=inner_residuals, effect_template=effect_template,
        trigger_effect_template=trigger_effect_template)
    if inner is None or not inner.exact or inner_residuals:
        return None
    plan = attached_granted_ability_plan(node=inner, quoted_text=quoted,
        oracle_id=record.oracle_id, face_id=face_id, source_line=span.line,
        card_name='Granted creature', keywords=())
    # Departure-trigger grants need an independently proven granted-source LKI
    # boundary. Keep this carrier to activated and mana abilities for now.
    if plan is None or plan.node_kind not in {'granted_activated_ability', 'granted_mana_ability'}:
        return None
    effect = {'op': 'apply_source_characteristics_until_end_of_turn', 'schema_version': 7,
        'card': '$target.0', 'keywords': [], 'ability_fragments': [dict(plan.fragment)],
        'exclude_source': False, 'printed_only': False}
    if query is not None:
        effect = {'op': effect['op'], 'schema_version': 8, 'predicate': query.to_dict(),
            'ability_fragments': effect['ability_fragments']}
    from ..rules.capabilities import capability_dependencies_for_node
    mechanics = (TEMPORARY_QUOTED_GRANT_MECHANIC, 'cr-611-continuous-effects',
        *(['cr-115-targets'] if target is not None else []))
    schema = target.to_target_schema() if target is not None else None
    dependencies = capability_dependencies_for_node(effects=(effect,), target_schema=schema,
        mechanic_ids=mechanics)
    if TEMPORARY_QUOTED_GRANT_CAPABILITY not in dependencies:
        return None
    gate = explicit_capabilities_gate(tuple(sorted(set(dependencies) | set(inner.capability_dependencies))),
        capability_registry=capability_registry, capability_profile=capability_profile)
    ids = (append_residual(residuals, kind='dependency_contract', text=line, span=span,
        reason='Temporary quoted grant requires its complete independent ability and duration owners.',
        blockers=gate.blockers),) if gate.blockers else ()
    if not set(printed_card_types).intersection({'instant', 'sorcery'}):
        return None
    outer = OracleNode(node_id=node_id, kind='spell_ability', text=line, span=span,
        active_zone='stack', event='resolve', lowerable=True, exact=not gate.blockers,
        template_id='temporary-quoted-ability-eot-v1', effects=(effect,),
        target_schema=schema, mechanics=mechanics, residual_ids=ids,
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
    return outer, replace(inner, node_id=node_id + ':granted', kind=plan.node_kind,
        text=quoted, active_zone='battlefield', residual_ids=())
