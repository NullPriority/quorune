"""Lower the exact life-paid roll modifier and final-six token trigger."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.night_shift_dice import (
    ADJUSTMENT_CAPABILITY, ADJUSTMENT_EVENT, FINAL_SIX_EVENT, TOKEN_DEPENDENCIES,
    adjustment_descriptor, final_six_token_effect, final_six_trigger_condition,
)
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual
from .static_runtime_nodes import runtime_handler_node
from .token_templates import fixed_token_creation_effect_template


_ADJUSTMENT = (
    "after you roll a die, you may pay 1 life. if you do, increase or decrease the result by 1. "
    "do this only once each turn."
)
_TOKEN = "create a 2/2 black Zombie Employee creature token."
_TRIGGER = "whenever you roll a 6, " + _TOKEN.casefold()


def night_shift_adjustment_node(*, material_line, **scope):
    if " ".join(material_line.casefold().split()) != _ADJUSTMENT:
        return None
    return runtime_handler_node(
        **scope, compiled=("post-roll-pay-life-adjust-one-v1", adjustment_descriptor(), ADJUSTMENT_CAPABILITY),
        kind="static_ability", event=ADJUSTMENT_EVENT, runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason="A post-roll life payment requires its original static modifier",
    )


def night_shift_token_node(*, node_id, line, material_line, span, capability_registry, capability_profile, residuals):
    if " ".join(material_line.casefold().split()) != _TRIGGER:
        return None
    template = fixed_token_creation_effect_template(_TOKEN)
    if template is None or template.effect != final_six_token_effect():
        return None
    gate = explicit_capabilities_gate(
        TOKEN_DEPENDENCIES, capability_registry=capability_registry, capability_profile=capability_profile,
    )
    residual_ids = (append_residual(
        residuals, kind="dependency_contract", text=line, span=span,
        reason="The final-six occurrence requires ordinary fixed token creation and trigger placement",
        blockers=gate.blockers,
    ),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span, active_zone="battlefield",
        event=FINAL_SIX_EVENT, event_condition=final_six_trigger_condition(), effects=(template.effect,),
        lowerable=True, exact=not residual_ids, template_id="controller-final-six-fixed-token-v1",
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        mechanics=("cr-603-handling-triggered-abilities", *template.mechanics),
        capability_dependencies=gate.capabilities, residual_ids=residual_ids,
        capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    )
