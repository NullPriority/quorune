"""Lower the complete artifact hand reveal and immediate mana production."""

from . import regex_working_set as re
from ..artifact_hand_mana_model import (
    ARTIFACT_HAND_MANA_CAPABILITY, ARTIFACT_HAND_MANA_HANDLER_ID, ArtifactHandActivatedManaSpec,
)
from ..public_quantity_mana_abilities import compile_mana_activation_costs
from .activated_costs import activated_ability_cost, activated_ability_cost_capabilities
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


def artifact_hand_mana_activated_node(ability, node_id, line, span, registry, profile, residuals):
    if not re.fullmatch(
        r"Reveal any number of artifact cards in your hand\. Add \{C\}\{C\} for each card revealed this way\.",
        " ".join(ability.effect_text.split()), re.I,
    ):
        return None
    cost = compile_mana_activation_costs(ability)
    if cost is None:
        return None
    try:
        spec = ArtifactHandActivatedManaSpec(cost)
    except ValueError:
        return None
    compiled = spec.to_activated_ability()
    gate = explicit_capabilities_gate(
        (ARTIFACT_HAND_MANA_CAPABILITY, *activated_ability_cost_capabilities(compiled)),
        capability_registry=registry, capability_profile=profile,
    )
    blockers = gate.blockers
    dependencies = (append_residual(
        residuals, kind="dependency_contract", text=line, span=span,
        reason="Private artifact hand mana requires its closed selection and immediate payment authority",
        blockers=blockers,
    ),) if blockers else ()
    return compiled, OracleNode(
        node_id=node_id, kind="mana_ability", text=line, span=span, active_zone="battlefield",
        event="activate", lowerable=True, exact=not blockers,
        template_id="activated-mana-artifact-hand-reveal-v1", cost=activated_ability_cost(compiled),
        handlers=({"handler_id": ARTIFACT_HAND_MANA_HANDLER_ID, "schema_version": 1, "event": "activate",
                   "ability": spec.costs.to_dict()},),
        mechanics=(), residual_ids=dependencies, capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None,
    )
