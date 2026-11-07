from __future__ import annotations

import re

from ..abilities import ActivatedAbility
from ..saddle import SADDLE_CAPABILITY, SADDLE_EFFECT_TEXT, SADDLE_OPERATION
from ..semantic_runtime.activated_abilities import activated_ability_catalog_descriptor
from ..util import normalize_mana_bundle
from .activated_costs import activated_ability_cost
from .dependency_gate import explicit_capability_gate
from .ir_model import OracleNode, append_residual


def ordinary_saddle_keyword_node(*, node_id, line, material_line, span, mechanics,
                                capability_registry, capability_profile, residuals):
    """Lower one fixed Saddle activation through the normal typed catalog."""
    if "saddle" not in mechanics:
        return None
    match = re.fullmatch(r"Saddle (0|[1-9][0-9]*)\.?", material_line, re.I)
    if match is None or mechanics != ("saddle",):
        residual_id = append_residual(residuals, kind="unsupported_saddle_cost", text=line, span=span,
            reason="Saddle requires one fixed nonnegative threshold",
            blockers=("variable, compound, or alternative Saddle costs",))
        return OracleNode(node_id=node_id, kind="activated_ability", text=line, span=span,
            active_zone="battlefield", event="activate", lowerable=False, exact=False,
            template_id="ordinary-saddle-residual-v1", mechanics=mechanics, residual_ids=(residual_id,))
    threshold = int(match[1])
    ability = ActivatedAbility(
        ability_id=f"ab{span.line}", line_index=span.line - 1, oracle_line=line,
        cost_text=f"Saddle {threshold}", effect_text=SADDLE_EFFECT_TEXT,
        zones=("battlefield",), mana=normalize_mana_bundle(None), sorcery_speed=True,
        saddle_threshold=threshold,
    )
    gate = explicit_capability_gate(SADDLE_CAPABILITY,
        capability_registry=capability_registry, capability_profile=capability_profile)
    residual_ids = (append_residual(residuals, kind="dependency_contract", text=line, span=span,
        reason="Saddle requires its typed payment and designation owner", blockers=gate.blockers),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="activated_ability", text=line, span=span, active_zone="battlefield",
        event="activate", lowerable=True, exact=not residual_ids, template_id="ordinary-saddle-activation-v1",
        cost=activated_ability_cost(ability), effects=({"op": SADDLE_OPERATION, "card": "$source.zone_object"},),
        handlers=(activated_ability_catalog_descriptor(ability),), mechanics=mechanics, residual_ids=residual_ids,
        capability_dependencies=gate.capabilities, capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    )
