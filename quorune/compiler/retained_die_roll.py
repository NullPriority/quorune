"""The source-spanned extra die replacement and its retained-roll observer."""

from . import regex_working_set as re
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.retained_die_roll import (
    REPLACEMENT_CAPABILITY, REPLACEMENT_EVENT, RETAINED_EVENT, TRIGGER_CAPABILITY,
    TRIGGER_TEMPLATE, replacement_descriptor, source_counter_condition, source_counter_effect,
)
from ..rules.source_references import SourceReferenceSpec
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual
from .static_runtime_nodes import runtime_handler_node


def extra_die_replacement_node(*, material_line, **scope):
    if " ".join(material_line.casefold().split()) != (
        "if you would roll one or more dice, instead roll that many dice plus one and ignore the lowest roll."
    ):
        return None
    return runtime_handler_node(
        **scope, compiled=("controller-extra-die-ignore-lowest-v1", replacement_descriptor(), REPLACEMENT_CAPABILITY),
        kind="replacement_effect", event=REPLACEMENT_EVENT, runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason="Extra dice require their current controller-scoped replacement authority",
    )


def retained_dice_counter_node(*, node_id, line, material_line, span, card_name,
                              capability_registry, capability_profile, residuals):
    source = re.fullmatch(r"Whenever you roll one or more dice, put a \+1/\+1 counter on (.+?)\.",
                          material_line, re.IGNORECASE)
    if source is None or not (SourceReferenceSpec(card_name).matches(source[1])
                              or source[1].casefold() == "this creature"):
        return None
    gate = explicit_capabilities_gate(
        (TRIGGER_CAPABILITY, "counter.producer.fixed_effect", "trigger.placement.apnap"),
        capability_registry=capability_registry, capability_profile=capability_profile,
    )
    residual_ids = (append_residual(residuals, kind="dependency_contract", text=line, span=span,
        reason="Retained dice require their exact occurrence and counter-placement authorities",
        blockers=gate.blockers),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span, active_zone="battlefield",
        event=RETAINED_EVENT, event_condition=source_counter_condition(), effects=(source_counter_effect(),),
        lowerable=True, exact=not residual_ids, template_id=TRIGGER_TEMPLATE,
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,), residual_ids=residual_ids,
        mechanics=("cr-603-handling-triggered-abilities",),
        capability_dependencies=gate.capabilities, capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    )
