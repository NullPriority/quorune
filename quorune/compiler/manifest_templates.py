from __future__ import annotations

"""Closed current-controller Manifest and Manifest dread instructions."""

import re
from ..manifest import MANIFEST_ATTACHMENT_VERSION, MANIFEST_COUNTERS_FIELD
from ..query_effect_amount_model import CastXAmountSpec


def manifest_effect_template(text: str):
    match = re.fullmatch(
        r'Manifest (?P<mode>the top card of your library|the top two cards of your library|dread)'
        r'(?P<twice> twice)?\.', text.strip(), re.I,
    )
    if match is None:
        return _manifest_counter_template(text)
    if match['twice'] and match['mode'].casefold() != 'dread':
        return None
    mode = {'dread': 'dread', 'the top two cards of your library': 'top_two'}.get(match['mode'].casefold(), 'top')
    return ('manifest-controller-' + mode + '-v1',
        tuple({'op': 'manifest_card', 'player': '$controller', 'mode': mode} for _ in range(2 if match['twice'] else 1)),
        None, ('manifest', *(('manifest dread',) if mode == 'dread' else ()), 'cr-701-manifest'))


def _manifest_counter_template(text):
    match = re.fullmatch(
        r"Manifest (?P<mode>the top card of your library|dread), then put "
        r"(?P<count>a|two|three|X) \+1/\+1 (?P<noun>counters?) on (?P<target>it|that creature)\.",
        text.strip(), re.I,
    )
    if match is None:
        return None
    mode = "dread" if match["mode"].casefold() == "dread" else "top"
    count = match["count"].casefold()
    if ((match["noun"].casefold() == "counter") != (count == "a")
            or (mode == "dread") != (match["target"].casefold() == "that creature")
            or mode == "dread" and count == "x"):
        return None
    amount = CastXAmountSpec().to_dict() if count == "x" else {"a": 1, "two": 2, "three": 3}[count]
    effect = {"op": "manifest_card", "player": "$controller", "mode": mode,
              MANIFEST_COUNTERS_FIELD: {"schema_version": 1, "count": amount}}
    mechanics = ("manifest", *(("manifest dread",) if mode == "dread" else ()), "cr-701-manifest")
    return "manifest-result-counters-v1", (effect,), None, mechanics


def manifest_equipment_entry_template(text: str):
    if re.fullmatch(r"Manifest dread, then attach this Equipment to that creature\.", text.strip(), re.I) is None:
        return None
    effect = {"op": "manifest_card", "player": "$controller", "mode": "dread", MANIFEST_ATTACHMENT_VERSION: 1}
    return "manifest-dread-attach-equipment-v1", (effect,), None, ("manifest", "manifest dread", "cr-701-manifest")


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
