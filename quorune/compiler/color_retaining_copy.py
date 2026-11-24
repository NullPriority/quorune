"""Compile the optional entry copy and its inherited, targeted upkeep trigger."""

from __future__ import annotations

from dataclasses import replace

from ..rules.color_retaining_copy import (
    RETAINED_COLOR_COPY_CAPABILITY, RETAINED_COLOR_UPKEEP_LABEL, RETAINED_COLOR_UPKEEP_TEMPLATE,
    retained_color_upkeep_condition, retained_color_upkeep_effect, retained_color_upkeep_target,
)
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_pattern
from ..token_copy_recipes import TokenCopyExceptionSpec
from . import regex_working_set as re
from .attached_granted_ability_nodes import attached_granted_ability_plan
from .entry_copy_templates import entry_copy_node
from .ir_model import OracleNode
from .optional_effect_templates import FIXED_OPTIONAL_EFFECT_CAPABILITY, FIXED_OPTIONAL_EFFECT_MECHANIC


def retained_color_copy_nodes(*, record, face_id, node_id, line, material_line, span,
        source_name, capability_registry, capability_profile):
    from ..entry_copy import EntryCopySpec
    from ..object_predicate import ObjectQuerySpec

    source = SourceReferenceSpec(source_name).regex_pattern
    receiver = rf"(?:{source}|{source_self_permanent_pattern()})"
    if re.fullmatch(
        rf'You may have {receiver} enter as a copy of any creature on the battlefield, '
        r'except it doesn[\'’]t copy that creature[\'’]s color and it has '
        rf'"At the beginning of your upkeep, you may have {receiver} become a copy of target creature, '
        r'except it doesn[\'’]t copy that creature[\'’]s color and it has this ability\.?"\.?',
        " ".join(material_line.split()), re.IGNORECASE,
    ) is None:
        return None
    dependencies = (RETAINED_COLOR_COPY_CAPABILITY, FIXED_OPTIONAL_EFFECT_CAPABILITY, "target.revalidate_resolution")
    closure = capability_registry.closure(dependencies, profile=capability_profile)
    if not closure.trusted or closure.blockers:
        return None
    child_id = f"{node_id}:quoted-ability:1"
    child = OracleNode(
        node_id=child_id, kind="triggered_ability", text=RETAINED_COLOR_UPKEEP_LABEL, span=span,
        event="step.begin", active_zone="battlefield", lowerable=True, exact=True,
        template_id=RETAINED_COLOR_UPKEEP_TEMPLATE, event_condition=retained_color_upkeep_condition(),
        target_schema=retained_color_upkeep_target(), effects=(retained_color_upkeep_effect(),),
        mechanics=("cr-115-targets", FIXED_OPTIONAL_EFFECT_MECHANIC), capability_dependencies=dependencies,
        capability_closure=closure.reachable, capability_profile=closure.profile,
        capability_fingerprint=closure.fingerprint,
    )
    granted = attached_granted_ability_plan(node=child, quoted_text=RETAINED_COLOR_UPKEEP_LABEL,
        oracle_id=record.oracle_id, face_id=face_id, source_line=span.line, card_name="Copied creature",
        keywords=(), node_id=child_id)
    if granted is None:
        return None
    spec = EntryCopySpec(ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
        schema_version=14, exception=TokenCopyExceptionSpec(add_ability_fragments=(granted.fragment,)))
    residuals = []
    parent = entry_copy_node(node_id=node_id, line=line, material_line=material_line, span=span,
        source_name=source_name, capability_registry=capability_registry, capability_profile=capability_profile,
        residuals=residuals, spec=spec)
    if parent is None or not parent.exact or residuals:
        return None
    return parent, replace(child, kind=granted.node_kind)
