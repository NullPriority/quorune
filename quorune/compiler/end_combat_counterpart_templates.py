"""Exact end-of-combat results over the source's live combat counterparts."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..end_combat_counterpart_model import (
    END_COMBAT_COUNTERPART_TEMPLATE, EndCombatCounterpartResult, EndCombatCounterpartSpec,
    end_combat_counterpart_condition,
)
from ..rules.capabilities import CapabilityRegistry
from ..rules.source_references import SourceReferenceSpec
from . import regex_working_set as re
from .dependency_gate import explicit_capabilities_gate
from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


def fixed_end_combat_counterpart_result(
    text: str, *, source_name: str,
) -> tuple[FixedPublicEventBindingSpec, EndCombatCounterpartSpec] | None:
    prefix = re.fullmatch(r"At end of combat, (?P<body>.+)", text.strip().removesuffix("."), re.I)
    if prefix is None:
        return None
    body = prefix["body"]
    match = re.fullmatch(
        r"(?P<result>destroy all creatures|put a -0/-2 counter on each creature) "
        r"blocking or blocked by (?P<source>.+)",
        body, re.I,
    )
    if match is None or (
        match["source"].casefold() != "this creature"
        and not SourceReferenceSpec(source_name).matches(match["source"])
    ):
        return None
    result = (
        EndCombatCounterpartResult.DESTROY if match["result"].casefold().startswith("destroy")
        else EndCombatCounterpartResult.PLACE_MINUS_ZERO_MINUS_TWO_COUNTER
    )
    return FixedPublicEventBindingSpec(
        event="step.begin", variant="end_combat_current_counterparts", body=body,
        template_id=END_COMBAT_COUNTERPART_TEMPLATE,
        mechanic="cr-603-handling-triggered-abilities",
        condition=end_combat_counterpart_condition(),
    ), EndCombatCounterpartSpec(result)


def end_combat_counterpart_node(
    *, node_id: str, line: str, material_line: str, span: SourceSpan, card_name: str,
    capability_registry: CapabilityRegistry | None, capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    template = fixed_end_combat_counterpart_result(material_line, source_name=card_name)
    if template is None:
        return None
    binding, spec = template
    gate = explicit_capabilities_gate(
        spec.capabilities, capability_registry=capability_registry, capability_profile=capability_profile,
    )
    residual_ids = ()
    if gate.blockers:
        residual_ids = (append_residual(
            residuals, kind="dependency_contract", text=line, span=span,
            reason="End-of-combat counterparts require the live combat query and simultaneous result owners",
            blockers=gate.blockers,
        ),)
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span,
        active_zone=binding.active_zone, event=binding.event, event_condition=binding.condition,
        lowerable=True, exact=not residual_ids, template_id=binding.template_id,
        effects=(spec.to_effect(),), mechanics=(binding.mechanic, "fixed-typed-event-effect-trigger"),
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,), residual_ids=residual_ids,
        capability_dependencies=gate.capabilities, capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    )
