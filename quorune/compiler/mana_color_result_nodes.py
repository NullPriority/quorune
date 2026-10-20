from __future__ import annotations

"""Whole spell clauses that define X from their own committed mana colors."""

from collections.abc import Mapping
from dataclasses import replace
import re

from ..mana_spent_amounts import MANA_COLOR_AMOUNT_CAPABILITY, MANA_COLOR_AMOUNT_MECHANIC
from ..query_effect_amount_model import CAST_X_AMOUNT_KIND, CastXAmountSpec
from ..rules.source_references import SourceReferenceSpec
from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec, SCALAR_AMOUNT_MECHANIC
from .declared_effect_amounts import declared_effect_amount_template
from .dependency_gate import dependency_gate
from .ir_model import OracleNode, append_residual


def mana_color_result_node(*, record, node_id, line, material_line, span, effect_template,
        trusted_mechanics, capability_registry, capability_profile, residuals):
    if ('colors of mana spent' not in material_line.casefold() or record.faces
            or not (record.is_instant or record.is_sorcery) or 'X' in record.mana_cost):
        return None
    material = re.sub(r'^Converge\s+—\s+', '', material_line.strip(), flags=re.I)
    source = SourceReferenceSpec(record.name).regex_pattern
    match = re.fullmatch(rf'(?P<body>.+?), where X is the number of colors of mana spent to cast '
        rf'(?:this spell|{source})\.(?P<tail>\s*(?:Then |That creature ).+)?', material, re.I)
    if match is None:
        return None
    tail = match['tail'] or ''
    if re.search(r'\bX\b', tail):
        return None
    def fixed_instruction(body):
        if tail.strip().casefold().startswith('then '):
            body = body.rstrip('.') + ', then ' + tail.strip()[5:]
        else:
            body += tail
        return effect_template(body, card_name=record.name)
    fixed = declared_effect_amount_template(match['body'] + '.', source_name=record.name,
        cast_x_available=True, compile_fixed=fixed_instruction)
    if fixed is None or (isinstance(fixed[2], Mapping) and 'count_parameter' in fixed[2]):
        return None
    amount = ScalarEffectAmountSpec(ScalarAmountOrigin.CAST_MANA_COLOR_COUNT, schema_version=6)

    def project(value):
        if isinstance(value, Mapping):
            if value.get('kind') == CAST_X_AMOUNT_KIND:
                return replace(amount, coefficient=CastXAmountSpec.from_dict(value).coefficient).to_dict()
            return {key: project(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(project(child) for child in value)
        return value

    effects = tuple(project(effect) for effect in fixed[1])
    mechanics = tuple(dict.fromkeys((*fixed[3], SCALAR_AMOUNT_MECHANIC, MANA_COLOR_AMOUNT_MECHANIC)))
    gate = dependency_gate(mechanics=mechanics, effects=effects, target_schema=fixed[2],
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, explicit_capabilities=(MANA_COLOR_AMOUNT_CAPABILITY,))
    ids = (append_residual(residuals, kind='dependency_contract', text=line, span=span,
        reason='Spent mana-color results require committed spell payment and complete result owners',
        blockers=gate.blockers),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(node_id=node_id, kind='spell_ability', text=line, span=span,
        active_zone='stack', event='resolve', lowerable=True, exact=not ids,
        template_id='spent-mana-color-count-result-v1', effects=effects, target_schema=fixed[2],
        mechanics=mechanics, residual_ids=ids, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None)
