"""Lower the source-spanned price, type and end-step rules of Impending."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..continuous_conditions import FIXED_PUBLIC_STATE_INTERVENING_COVERAGE
from ..impending_model import (
    IMPENDING_CAPABILITY,
    IMPENDING_COST_EVENT,
    IMPENDING_HANDLER_ID,
    parse_fixed_mana_impending,
)
from ..rules.impending import impending_end_step_condition, impending_end_step_effect, impending_time_counter_condition
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode
from .static_runtime_nodes import runtime_handler_node


def fixed_mana_impending_nodes(*, node_id, line, material_line, span, card_types,
                               capability_registry, capability_profile, residuals):
    spec = parse_fixed_mana_impending(material_line)
    if spec is None or set(card_types) != {"creature", "enchantment"}:
        return ()
    descriptor = {
        "handler_id": IMPENDING_HANDLER_ID,
        "event": IMPENDING_COST_EVENT,
        "schema_version": 1,
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True,
        "impending": spec.to_dict(),
    }
    price = runtime_handler_node(
        node_id=f"{node_id}:impending-price", line=line, span=span,
        compiled=("fixed-mana-impending-price-v1", descriptor, IMPENDING_CAPABILITY),
        kind="static_ability", event=IMPENDING_COST_EVENT, active_zone="stack",
        runtime_coverage=("fixed_mana_impending_price",),
        dependency_reason="Impending requires the complete paid-entry, layer and end-step contracts",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
    from ..semantic_runtime.fixed_characteristic_settings import (
        CREATURE_TYPE_REMOVAL_CAPABILITY, CREATURE_TYPE_REMOVAL_HANDLER,
    )

    layer_descriptor = {
        "handler_id": CREATURE_TYPE_REMOVAL_HANDLER,
        "schema_version": 1,
        "event": "characteristics.evaluate",
        "source_condition": impending_time_counter_condition().to_dict(),
    }
    characteristic = runtime_handler_node(
        node_id=f"{node_id}:type", line=line, span=span,
        compiled=(
            "impending-creature-type-v1", layer_descriptor,
            (IMPENDING_CAPABILITY, CREATURE_TYPE_REMOVAL_CAPABILITY),
        ),
        kind="static_ability", event="characteristics.evaluate", active_zone="battlefield",
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason="Impending requires its exact layer-four and lifetime contracts",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
    end_step = _impending_end_step_node(
        node_id=node_id, line=line, span=span, capability_registry=capability_registry,
        capability_profile=capability_profile, residual_ids=price.residual_ids,
    )
    return price, characteristic, end_step


def _impending_end_step_node(*, node_id, line, span, capability_registry, capability_profile, residual_ids):
    gate = explicit_capabilities_gate(
        (IMPENDING_CAPABILITY, "counter.removal.fixed_effect", "counter.removal.fixed_source_effect",
         "trigger.placement.apnap"),
        capability_registry=capability_registry, capability_profile=capability_profile,
    )
    closure = gate.closure
    return OracleNode(
        node_id=f"{node_id}:end-step", kind="triggered_ability", text=line, span=span,
        active_zone="battlefield", event="step.begin", event_condition=impending_end_step_condition(),
        lowerable=True, exact=not gate.blockers, template_id="impending-time-counter-end-step-v1",
        effects=(impending_end_step_effect(),), mechanics=("cr-122-counters",),
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE, "intervening_condition",
                          FIXED_PUBLIC_STATE_INTERVENING_COVERAGE),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure is not None else (),
        capability_profile=closure.profile if closure is not None else None,
        capability_fingerprint=closure.fingerprint if closure is not None else None,
    )
