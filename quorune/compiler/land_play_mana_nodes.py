from __future__ import annotations

"""Compile the exact current-turn land-play alternative before generic mana rules."""

from ..land_play_mana_model import (
    LAND_PLAY_MANA_CAPABILITY, LAND_PLAY_MANA_HANDLER_ID, LandPlayActivatedManaSpec, LandPlayManaOutput,
)
from ..public_quantity_mana_abilities import compile_mana_activation_costs
from . import regex_working_set as re
from .activated_costs import activated_ability_cost
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


_PRINTED = re.compile(
    r"Add \{(?P<ordinary>[WUBRGC])\}\. If you played a land this turn, add \{(?P<played>[WUBRGC])\} instead\.",
    re.IGNORECASE,
)


def compile_land_play_mana(ability):
    match = _PRINTED.fullmatch(" ".join(ability.effect_text.split()))
    if match is None:
        return None
    costs = compile_mana_activation_costs(ability)
    if costs is None:
        return None
    try:
        return LandPlayActivatedManaSpec(
            costs, LandPlayManaOutput(match["ordinary"].upper(), match["played"].upper()),
        )
    except ValueError:
        return None


def land_play_activated_mana_node(ability, node_id, line, span, capability_registry, capability_profile, residuals):
    spec = compile_land_play_mana(ability)
    if spec is None:
        return None
    gate = explicit_capabilities_gate((LAND_PLAY_MANA_CAPABILITY,), capability_registry=capability_registry,
                                      capability_profile=capability_profile)
    residual_ids = (append_residual(residuals, kind="dependency_contract", text=line, span=span,
        reason="Land-play mana lacks the typed current-turn history and output authority", blockers=gate.blockers),) if gate.blockers else ()
    return OracleNode(
        node_id=node_id, kind="mana_ability", text=line, span=span, active_zone="battlefield", event="activate",
        lowerable=True, exact=not gate.blockers, template_id="activated-mana-land-play-output-v1",
        cost=activated_ability_cost(ability),
        handlers=({"handler_id": LAND_PLAY_MANA_HANDLER_ID, "schema_version": 1, "event": "activate",
                   "ability": spec.to_dict()},),
        mechanics=(), residual_ids=residual_ids, capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None,
    )
