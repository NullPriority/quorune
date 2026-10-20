from __future__ import annotations

"""Complete count-result clauses linked to the printed Multikicker instance."""

from collections.abc import Mapping
from dataclasses import replace
import re

from ..multikicker_result_amounts import MULTIKICKER_AMOUNT_CAPABILITY, MULTIKICKER_AMOUNT_MECHANIC
from ..query_effect_amount_model import CAST_X_AMOUNT_KIND, CastXAmountSpec
from ..rules.source_references import SourceReferenceSpec
from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec, SCALAR_AMOUNT_MECHANIC
from .declared_effect_amounts import declared_effect_amount_template
from .dependency_gate import dependency_gate
from .ir_model import OracleNode, append_residual
from .multikicker_nodes import _printed_cost


def _result_body(text, source_name, *, source_event):
    source = SourceReferenceSpec(source_name).regex_pattern
    reference = rf'(?:{source}|this creature|this permanent|it)' if source_event else rf'(?:{source}|this spell)'
    each = re.fullmatch(rf'(?P<body>.+?) for each time (?P<source>{reference}) was kicked\.', text, re.I)
    if each is not None:
        body = each['body']
        prefix = ''
        if '. ' in body:
            prefix, body = body.rsplit('. ', 1)
            prefix += '. '
        if re.search(r'\bX\b', prefix):
            return None
        coefficient = 1
        if re.fullmatch(r'Draw a card', body, re.I):
            return prefix, 'Draw X cards.', coefficient
        life = re.fullmatch(r'You gain (?P<amount>[12]) life', body, re.I)
        if life is not None:
            return prefix, 'You gain X life.', int(life['amount'])
        token = re.fullmatch(r'Create a (?P<token>.+?) creature token', body, re.I)
        if token is not None:
            return prefix, f"Create X {token['token']} creature tokens.", coefficient
    damage = re.fullmatch(rf'(?P<source>{reference}) deals damage to (?P<target>.+?) equal to '
        rf'(?P<twice>twice )?the number of times (?P<paid>{reference}) was kicked\.', text, re.I)
    if damage is not None:
        return '', f"{source_name} deals X damage to {damage['target']}.", 2 if damage['twice'] else 1
    return None


def multikicker_result_node(*, record, node_id, line, material_line, span, effect_template,
        trusted_mechanics, capability_registry, capability_profile, residuals):
    if 'kicked' not in material_line.casefold():
        return None
    cost = _printed_cost(record)
    if cost is None:
        return None
    source = SourceReferenceSpec(record.name).regex_pattern
    self_reference = rf'(?:{source}|this permanent' + ('|this creature' if record.is_creature else '') + ')'
    entry = re.fullmatch(rf'When {self_reference} enters, (?P<body>.+)', material_line, re.I) if record.is_permanent_spell else None
    if entry is None and not (record.is_instant or record.is_sorcery):
        return None
    body = entry['body'] if entry is not None else material_line
    result = _result_body(body, record.name, source_event=entry is not None)
    if result is None:
        return None
    prefix, variable, coefficient = result
    fixed = declared_effect_amount_template(variable, source_name=record.name, cast_x_available=True,
        compile_fixed=lambda text: effect_template(prefix + text, card_name=record.name))
    if fixed is None:
        return None
    amount = ScalarEffectAmountSpec(ScalarAmountOrigin.MULTIKICKER_COUNT, schema_version=5,
        multikicker=cost, coefficient=coefficient)

    def project(value):
        if isinstance(value, Mapping):
            if value.get('kind') == CAST_X_AMOUNT_KIND:
                sign = CastXAmountSpec.from_dict(value).coefficient
                return replace(amount, coefficient=coefficient * sign).to_dict()
            return {key: project(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(project(child) for child in value)
        return value

    effects = tuple(project(effect) for effect in fixed[1])
    mechanics = tuple(dict.fromkeys((*fixed[3], SCALAR_AMOUNT_MECHANIC, MULTIKICKER_AMOUNT_MECHANIC,
        *(('cr-603-handling-triggered-abilities', 'trigger-event-normalized-zone-change') if entry else ()))))
    gate = dependency_gate(mechanics=mechanics, effects=effects, target_schema=fixed[2],
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, explicit_capabilities=(MULTIKICKER_AMOUNT_CAPABILITY,))
    ids = (append_residual(residuals, kind='dependency_contract', text=line, span=span,
        reason='Linked Multikicker results require paid-instance and complete result authority', blockers=gate.blockers),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(node_id=node_id, kind='triggered_ability' if entry else 'spell_ability',
        text=line, span=span, active_zone='battlefield' if entry else 'stack',
        event='permanent.enter.self' if entry else 'resolve', lowerable=True, exact=not ids,
        template_id='linked-multikicker-count-result-v1', effects=effects, target_schema=fixed[2],
        mechanics=mechanics, residual_ids=ids, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None)
