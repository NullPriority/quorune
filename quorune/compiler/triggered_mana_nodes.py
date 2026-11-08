from __future__ import annotations

"""Lower fixed triggered mana before ordinary stack-trigger composition."""

import re
from collections import Counter

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..fixed_mana_abilities import FixedManaMode
from ..triggered_mana_model import (
    FIXED_TRIGGERED_MANA_CAPABILITY,
    FIXED_TRIGGERED_MANA_EVENT,
    FIXED_TRIGGERED_MANA_FIELD,
    FixedTriggeredManaSpec,
)
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


_SUBJECT = r"permanent|land|creature|plains|island|swamp|mountain|forest"
_OUTPUT = r"(?:an additional )?(?P<output>(?:\{[WUBRGC]\})+)\."
_OWN_TAP = re.compile(
    rf"(?:When|Whenever) you tap (?:a|an) (?P<subject>{_SUBJECT}) for (?:mana|\{{(?P<color>[WUBRGC])\}}), add {_OUTPUT}", re.I,
)
_PASSIVE_TAP = re.compile(
    rf"(?:When|Whenever) (?:(?P<attached>enchanted land)|(?:a|an) (?P<subject>{_SUBJECT})) "
    rf"is tapped for mana, its controller adds {_OUTPUT}", re.I,
)
_ANY_PLAYER_TAP = re.compile(
    rf"(?:When|Whenever) a player taps (?:a|an) (?P<subject>{_SUBJECT}) for mana, that player adds {_OUTPUT}", re.I,
)


def fixed_triggered_mana_node(*, node_id, line, material_line, span, capability_registry, capability_profile, residuals):
    match = _OWN_TAP.fullmatch(material_line)
    if match is not None:
        scope, subject, color = "controller", match["subject"].casefold(), match["color"]
    elif match := _PASSIVE_TAP.fullmatch(material_line):
        scope = "attached" if match["attached"] else "any"
        subject, color = (match["subject"] or "land").casefold(), None
    elif match := _ANY_PLAYER_TAP.fullmatch(material_line):
        scope, subject, color = "any", match["subject"].casefold(), None
    else:
        return None
    output = FixedManaMode.from_bundle(Counter(re.findall(r"\{([WUBRGC])\}", match["output"].upper())))
    spec = FixedTriggeredManaSpec(scope, subject, output, color.upper() if color else None)
    gate = explicit_capabilities_gate((FIXED_TRIGGERED_MANA_CAPABILITY,), capability_registry=capability_registry,
                                     capability_profile=capability_profile)
    residual_ids = (append_residual(residuals, kind="dependency_contract", text=line, span=span,
        reason="Fixed triggered mana requires its production and funding owner", blockers=gate.blockers),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span, active_zone="battlefield",
        event=FIXED_TRIGGERED_MANA_EVENT, event_condition={FIXED_TRIGGERED_MANA_FIELD: spec.to_dict()},
        lowerable=True, exact=not residual_ids, template_id="fixed-triggered-mana-v1",
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,), residual_ids=residual_ids,
        capability_dependencies=gate.capabilities, capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None, capability_fingerprint=closure.fingerprint if closure else None,
    )
