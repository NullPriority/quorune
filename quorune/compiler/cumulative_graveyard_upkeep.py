"""Source-spanned grouped graveyard costs over the existing upkeep owner."""

from ..cumulative_graveyard_upkeep import compile_graveyard_library_upkeep
from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
from ..rules.cumulative_object_upkeep import CONTROLLER_UPKEEP
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


def cumulative_graveyard_upkeep_node(*, node_id, line, material_line, span, mechanics,
                                    capability_registry, capability_profile, residuals):
    if mechanics != ('cumulative upkeep',):return None
    spec = compile_graveyard_library_upkeep(material_line)
    if spec is None:return None
    gate = explicit_capabilities_gate(spec.capabilities, capability_registry=capability_registry,
                                     capability_profile=capability_profile)
    blocked = () if not gate.blockers else (append_residual(residuals, kind='dependency_contract',
        text=line, span=span, reason='Grouped graveyard upkeep lacks its complete cost and source owners', blockers=gate.blockers),)
    return OracleNode(node_id=node_id, kind='triggered_ability', text=line, span=span,
        active_zone='battlefield', event='step.begin', event_condition=CONTROLLER_UPKEEP,
        lowerable=True, exact=not blocked, template_id='cumulative-graveyard-library-upkeep-v1',
        effects=(spec.effect(),), mechanics=(*mechanics, FIXED_EFFECT_PAYMENT_MECHANIC), residual_ids=blocked,
        capability_dependencies=gate.capabilities, capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
