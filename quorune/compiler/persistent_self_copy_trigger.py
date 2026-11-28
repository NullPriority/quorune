from __future__ import annotations

"""Compile a targeted upkeep trigger that copies its own permanent."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..replacement.face_status_copy import upkeep_refacing_condition
from ..rules.persistent_self_copy_trigger import (
    PERSISTENT_SELF_COPY_TRIGGER_DEPENDENCIES, PERSISTENT_SELF_COPY_TRIGGER_TEMPLATE,
    self_copy_upkeep_effect, self_copy_upkeep_target,
)
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_pattern
from . import regex_working_set as re
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual
from .optional_effect_templates import FIXED_OPTIONAL_EFFECT_MECHANIC


def copy_trigger_node(*, printed_card_types, **scope):
    from .attack_trigger_copy_nodes import attack_trigger_copy_node
    from .commander_cast_copy_nodes import cast_source_copy_node

    return (
        attack_trigger_copy_node(**scope)
        or cast_source_copy_node(printed_card_types=printed_card_types, **scope)
        or _persistent_self_copy_trigger_node(**scope)
    )


def _persistent_self_copy_trigger_node(*, node_id, line, material_line, span,
                                      source_name, capability_registry, capability_profile, residuals):
    source = SourceReferenceSpec(source_name).regex_pattern
    receiver = rf"(?:{source}|{source_self_permanent_pattern()})"
    if not re.fullmatch(
        rf"At the beginning of your upkeep, you may have {receiver} become a copy of another target creature, "
        r"except it has this ability\.",
        material_line.strip(), re.IGNORECASE,
    ):
        return None
    gate = explicit_capabilities_gate(PERSISTENT_SELF_COPY_TRIGGER_DEPENDENCIES,
        capability_registry=capability_registry, capability_profile=capability_profile)
    blocked = (append_residual(residuals, kind="dependency_contract", text=line, span=span,
        reason="Persistent self-copy requires the exact upkeep trigger and copiable ability grant",
        blockers=gate.blockers),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span, active_zone="battlefield",
        event="step.begin", event_condition=upkeep_refacing_condition(), target_schema=self_copy_upkeep_target(),
        effects=(self_copy_upkeep_effect(),), lowerable=True, exact=not blocked,
        template_id=PERSISTENT_SELF_COPY_TRIGGER_TEMPLATE, residual_ids=blocked,
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        mechanics=("cr-603-handling-triggered-abilities", "cr-115-targets", "cr-707-copying-objects",
                   FIXED_OPTIONAL_EFFECT_MECHANIC),
        capability_dependencies=gate.capabilities, capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    )
