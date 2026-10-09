from __future__ import annotations

"""Source-incarnation maintenance on entry or a fixed turn step."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..rules.capabilities import CapabilityRegistry
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from ..source_maintenance import SourceMaintenanceSpec, SOURCE_MAINTENANCE_MECHANIC
from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
from .fixed_effect_payment_templates import fixed_effect_payment_spec
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


_STEP = re.compile(r"At the beginning of (?P<step>your upkeep|your end step|each upkeep|the end step), (?P<body>.+)", re.I)
_ENTRY = re.compile(r"When (?P<subject>.+?) enters, (?P<body>.+)", re.I)
_BODY = re.compile(r"sacrifice (?P<subject>.+?)(?: unless you (?P<payment>.+?))?\.?$", re.I)


@dataclass(frozen=True, slots=True)
class SourceMaintenanceTemplate:
    spec: SourceMaintenanceSpec
    event: str
    condition: Mapping[str, Any] | None


def source_maintenance_template(text: str, *, card_name: str) -> SourceMaintenanceTemplate | None:
    step = _STEP.fullmatch(text.strip())
    entry = _ENTRY.fullmatch(text.strip()) if step is None else None
    match = step or entry
    if match is None:
        return None
    reference = SourceReferenceSpec(card_name)
    def is_source(subject: str) -> bool:
        return source_self_permanent_type(subject) is not None or reference.matches(subject)
    if entry is not None and not is_source(entry["subject"]):
        return None
    body = _BODY.fullmatch(match["body"])
    if body is None or not (is_source(body["subject"]) or entry is not None and body["subject"].casefold() == "it"):
        return None
    payment = fixed_effect_payment_spec(body["payment"]) if body["payment"] is not None else None
    if body["payment"] is not None and payment is None:
        return None
    spec = SourceMaintenanceSpec(payment)
    if entry is not None:
        return SourceMaintenanceTemplate(spec, "permanent.enter.self", None)
    variant = step["step"].casefold()
    conditions = [{"field": "step", "op": "eq", "value": "upkeep" if "upkeep" in variant else "end_step"}]
    if variant.startswith("your"):
        conditions.insert(0, {"field": "player", "op": "eq", "value": "$source.controller"})
    return SourceMaintenanceTemplate(spec, "step.begin", {"all": conditions})


def source_maintenance_node(*, node_id: str, line: str, material_line: str,
    span: SourceSpan, card_name: str, capability_registry: CapabilityRegistry | None,
    capability_profile: str, residuals: list[OracleResidual]) -> OracleNode | None:
    template = source_maintenance_template(material_line, card_name=card_name)
    if template is None:
        return None
    gate = explicit_capabilities_gate(template.spec.capabilities,
        capability_registry=capability_registry, capability_profile=capability_profile)
    residual_ids = ()
    if gate.blockers:
        residual_ids = (append_residual(residuals, kind="dependency_contract", text=line,
            span=span, reason="source maintenance requires verified fixed payment and sacrifice owners", blockers=gate.blockers),)
    closure = gate.closure
    return OracleNode(node_id=node_id, kind="triggered_ability", text=line, span=span,
        active_zone="battlefield", event=template.event, event_condition=template.condition,
        lowerable=True, exact=not residual_ids, template_id="fixed-source-maintenance-v1",
        effects=(template.spec.effect(),), mechanics=(SOURCE_MAINTENANCE_MECHANIC,
            FIXED_EFFECT_PAYMENT_MECHANIC, "cr-603-handling-triggered-abilities"),
        residual_ids=residual_ids, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None)
