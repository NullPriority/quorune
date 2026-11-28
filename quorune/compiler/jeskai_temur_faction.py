"""Lower both faction branches only with the original full entry construction."""

from ..rules.jeskai_temur_faction import (
    JESKAI_CAPABILITIES, JESKAI_COVERAGE, JESKAI_EVENT, TEMUR_CAPABILITIES,
    jeskai_condition, jeskai_effect, temur_descriptor,
)
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual
from .static_runtime_nodes import runtime_handler_node


ENTRY = "as this enchantment enters, choose jeskai or temur."
_JESKAI = "• jeskai — whenever one or more creatures you control deal combat damage to a player, draw a card."
_TEMUR = "• temur — creatures you control get +1/+0 and have trample and haste."


def jeskai_temur_face_compiler(fallback, source):
    if source != (ENTRY, _JESKAI, _TEMUR):
        return fallback

    def compile_ability(**fields):
        line = " ".join(fields["line"].casefold().split())
        if line not in {_JESKAI, _TEMUR}:
            return fallback(**fields)
        subset = ("node_id", "line", "span", "capability_registry", "capability_profile", "residuals")
        args = {key: fields[key] for key in subset}
        if line == _TEMUR:
            return runtime_handler_node(
                **args, compiled=("entry-faction-temur-v1", temur_descriptor(), TEMUR_CAPABILITIES),
                kind="static_ability", event="characteristics.evaluate",
                dependency_reason="The Temur branch requires its original entry choice and fixed creature grant",
            )
        return _jeskai_trigger_node(**args)

    return compile_ability


def _jeskai_trigger_node(*, node_id, line, span, capability_registry, capability_profile, residuals):
    gate = explicit_capabilities_gate(
        JESKAI_CAPABILITIES, capability_registry=capability_registry, capability_profile=capability_profile,
    )
    residual = ()
    if gate.blockers:
        residual = (append_residual(
            residuals, kind="dependency_contract", text=line, span=span,
            reason="The Jeskai branch requires its entry choice and grouped dealt damage",
            blockers=gate.blockers,
        ),)
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span,
        active_zone="battlefield", event=JESKAI_EVENT, event_condition=jeskai_condition(),
        lowerable=True, exact=not residual, template_id="entry-faction-jeskai-combat-draw-v1",
        effects=(jeskai_effect(),), runtime_coverage=JESKAI_COVERAGE,
        mechanics=("cr-603-handling-triggered-abilities", "cr-121-drawing-a-card"),
        residual_ids=residual, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure is not None else (),
        capability_profile=closure.profile if closure is not None else None,
        capability_fingerprint=closure.fingerprint if closure is not None else None,
    )
