from __future__ import annotations

"""Closed current-controller Manifest and Manifest dread instructions."""

import re


def manifest_effect_template(text: str):
    match = re.fullmatch(r'Manifest (?P<mode>the top card of your library|dread)(?P<twice> twice)?\.', text.strip(), re.I)
    if match is None or match['twice'] and match['mode'].casefold() != 'dread':
        return None
    mode = 'dread' if match['mode'].casefold() == 'dread' else 'top'
    return ('manifest-controller-' + mode + '-v1',
        tuple({'op': 'manifest_card', 'player': '$controller', 'mode': mode} for _ in range(2 if match['twice'] else 1)),
        None, ('manifest', *(('manifest dread',) if mode == 'dread' else ()), 'cr-701-manifest'))


def manifest_keyword_node(*, node_id, line, material_line, span, capability_registry, capability_profile, **_unused):
    from .dependency_gate import dependency_gate
    from .ir_model import OracleNode
    compiled = manifest_effect_template(material_line.rstrip('.') + '.')
    if compiled is None:
        return None
    template, effects, schema, mechanics = compiled
    gate = dependency_gate(mechanics=mechanics, effects=effects, target_schema=schema,
        trusted_mechanics=frozenset(), capability_registry=capability_registry, capability_profile=capability_profile)
    if gate.blockers or gate.closure is None:
        return None
    return OracleNode(node_id=node_id, kind='spell_ability', text=line, span=span, active_zone='stack',
        event='resolve', exact=True, lowerable=True, template_id=template, effects=effects, mechanics=mechanics,
        capability_dependencies=gate.capabilities, capability_closure=gate.closure.reachable,
        capability_profile=gate.closure.profile, capability_fingerprint=gate.closure.fingerprint)
