"""Compile the two closed linked landfall play and end-step recipes."""

from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual
from ..rules.linked_landfall_play import (
    EXPLORE_EXILE_CAPABILITY, EXPLORE_EXILE_MECHANIC, EXPLORE_SETTLE_CAPABILITY, EXPLORE_SETTLE_MECHANIC,
    exploration_end_step_condition, exploration_exile_effect, exploration_settlement_effect,
)


def linked_landfall_play_node(*, node_id, line, material_line, span, capability_registry, capability_profile, residuals):
    text = " ".join(material_line.split()).casefold().replace("’", "'")
    if text.removeprefix("landfall — ") == ("whenever a land you control enters, exile the top card of your library. "
                "you may play that card for as long as it remains exiled."):
        event, effect, mechanic, capability = "land.enter", exploration_exile_effect(), EXPLORE_EXILE_MECHANIC, EXPLORE_EXILE_CAPABILITY
        condition = {"field":"controller","op":"eq","value":"$source.controller"}
        coverage = ()
    elif text == ("at the beginning of your end step, if there are cards exiled with this enchantment, "
                  "put them into their owner's graveyard, then this enchantment deals that much damage to each opponent."):
        event, effect, mechanic, capability = "step.begin", exploration_settlement_effect(), EXPLORE_SETTLE_MECHANIC, EXPLORE_SETTLE_CAPABILITY
        condition = exploration_end_step_condition()
        coverage = ("intervening_condition",)
    else:
        return None
    gate = explicit_capabilities_gate((capability,),capability_registry=capability_registry,
                                      capability_profile=capability_profile)
    blocked = (append_residual(residuals,kind="dependency_contract",text=line,span=span,
        reason="The linked landfall instruction needs its typed exile and return authority",blockers=gate.blockers),) if gate.blockers else ()
    return OracleNode(node_id=node_id,kind="triggered_ability",text=line,span=span,active_zone="battlefield",
        event=event,event_condition=condition,lowerable=True,exact=not blocked,
        template_id=mechanic+"-v1",effects=(effect,),mechanics=(mechanic,),runtime_coverage=coverage,
        residual_ids=blocked,capability_dependencies=gate.closure.reachable if gate.closure else gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
