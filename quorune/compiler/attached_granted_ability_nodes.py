from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Callable, Mapping, Sequence

from ..attachment_references import AttachmentReferenceKind
from ..abilities import ActivatedAbility, parse_activated_abilities
from ..ability_fragments import (
    GrantedActivatedAbilitySpec,
    GrantedTriggeredAbilitySpec,
    ability_fragment_to_dict,
)
from ..fixed_mana_abilities import compile_fixed_activated_mana_ability
from ..carddb import CardRecord
from ..rules.capabilities import CapabilityRegistry
from .continuous_templates import (
    attached_quoted_ability_handler,
    attached_quoted_ability_text,
)
from .ir_model import OracleNode, OracleResidual, SourceSpan
from .static_runtime_nodes import runtime_handler_node


GRANTED_ACTIVATED_ABILITY_KIND = "granted_activated_ability"
GRANTED_MANA_ABILITY_KIND = "granted_mana_ability"
GRANTED_TRIGGERED_ABILITY_KIND = "granted_triggered_ability"
GRANTED_ABILITY_KINDS = frozenset(
    {
        GRANTED_ACTIVATED_ABILITY_KIND,
        GRANTED_MANA_ABILITY_KIND,
        GRANTED_TRIGGERED_ABILITY_KIND,
    }
)
_EXTERNAL_ATTACHMENT_REFERENCE = re.compile(
    r"\b(?:this|that) (?:Aura|Equipment|Fortification)\b"
    r"|\b(?:enchanted|equipped|fortified)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AttachedGrantedAbilityPlan:
    """One exact inner program and its layer-6 typed grant fragment."""

    node_kind: str
    ability_id: str
    semantic_key: str
    fragment: Mapping[str, Any]


def attached_granted_program_ability_id(
    *,
    kind: str,
    face_id: str,
    line: int,
) -> str | None:
    if kind in {
        GRANTED_ACTIVATED_ABILITY_KIND,
        GRANTED_MANA_ABILITY_KIND,
    }:
        return f"ability:granted:{face_id}:n{line}"
    if kind == GRANTED_TRIGGERED_ABILITY_KIND:
        return f"trigger:{face_id}:n{line}:granted"
    return None


def _closed_granted_activation(ability: ActivatedAbility) -> bool:
    """Keep grants inside the current v1 granted-activation schema."""

    return bool(
        ability.zones == ("battlefield",)
        and ability.compiled_cost
        and not ability.complex_symbols
        and not ability.untap_source
        and not ability.discard_source
        and not ability.sacrifice_source
        and not ability.exile_source
        and ability.life_payment == 0
        and ability.energy_payment == 0
        and ability.loyalty_delta is None
        and not ability.choices
        and not ability.uncompiled_costs
        and ability.generic_reduction_per_legendary_creature == 0
        and ability.crew_threshold is None
        and ability.color_set_mana_output is None
        and ability.activation_limit is None
        and not ability.library_search_types
        and not ability.activation_conditions
        and ability.dynamic_mana_output is None
        and ability.mana_spend_restriction is None
    )


def attached_granted_ability_plan(
    *,
    node: Any,
    quoted_text: str,
    oracle_id: str,
    face_id: str,
    source_line: int,
    card_name: str,
    keywords: Sequence[str],
) -> AttachedGrantedAbilityPlan | None:
    """Build one closed typed grant from an independently exact inner node."""

    if not node.exact or node.residual_ids:
        return None
    if node.kind in {"activated_ability", "mana_ability"}:
        abilities = parse_activated_abilities(
            card_name=card_name,
            oracle_text=quoted_text,
            keywords=keywords,
        )
        if len(abilities) != 1 or not _closed_granted_activation(abilities[0]):
            return None
        ability = abilities[0]
        fixed_mana = (
            compile_fixed_activated_mana_ability(ability)
            if ability.mana_ability
            else None
        )
        if ability.mana_ability and fixed_mana is None:
            return None
        node_kind = (
            GRANTED_MANA_ABILITY_KIND
            if ability.mana_ability
            else GRANTED_ACTIVATED_ABILITY_KIND
        )
        ability_id = attached_granted_program_ability_id(
            kind=node_kind,
            face_id=face_id,
            line=source_line,
        )
        if ability_id is None:
            return None
        semantic_key = f"{oracle_id}:{ability_id}"
        fragment = ability_fragment_to_dict(
            GrantedActivatedAbilitySpec(
                ability_id=ability_id,
                semantic_key=semantic_key,
                cost_text=ability.cost_text,
                effect_text=ability.effect_text,
                mana=tuple(ability.mana.items()),
                tap_source=ability.tap_source,
                sorcery_speed=ability.sorcery_speed,
                mana_ability=ability.mana_ability,
                fixed_mana_outputs=tuple(
                    tuple(mode.bundle.items())
                    for mode in (
                        fixed_mana.modes if fixed_mana is not None else ()
                    )
                ),
            )
        )
        return AttachedGrantedAbilityPlan(
            node_kind=node_kind,
            ability_id=ability_id,
            semantic_key=semantic_key,
            fragment=fragment,
        )
    if node.kind != "triggered_ability" or not node.event:
        return None
    node_kind = GRANTED_TRIGGERED_ABILITY_KIND
    ability_id = attached_granted_program_ability_id(
        kind=node_kind,
        face_id=face_id,
        line=source_line,
    )
    if ability_id is None:
        return None
    semantic_key = f"{oracle_id}:{ability_id}"
    return AttachedGrantedAbilityPlan(
        node_kind=node_kind,
        ability_id=ability_id,
        semantic_key=semantic_key,
        fragment=ability_fragment_to_dict(
            GrantedTriggeredAbilitySpec(
                ability_id=ability_id,
                semantic_key=semantic_key,
                event=node.event,
                label=quoted_text,
            )
        ),
    )


def compile_attached_granted_ability_nodes(
    *,
    record: CardRecord,
    face_id: str,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    source_name: str,
    source_attachment_relation: AttachmentReferenceKind | None,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    compile_inner: Callable[..., OracleNode | None],
    effect_template: Any,
    trigger_effect_template: Any,
    material_line_for: Callable[[str], str],
) -> tuple[OracleNode, OracleNode] | None:
    """Compile one accepted attached shell and one independent inner ability."""

    if source_attachment_relation not in {
        AttachmentReferenceKind.ENCHANTED,
        AttachmentReferenceKind.EQUIPPED,
    }:
        return None
    quoted = attached_quoted_ability_text(
        material_line,
        source_name=source_name,
    )
    if quoted is None or _EXTERNAL_ATTACHMENT_REFERENCE.search(quoted):
        return None
    normalized_name = source_name.casefold().strip()
    if normalized_name and normalized_name in quoted.casefold():
        return None
    inner_residuals: list[OracleResidual] = []
    inner = compile_inner(
        node_id=f"{node_id}:granted",
        line=quoted,
        material_line=material_line_for(quoted),
        span=span,
        card_name="Granted creature",
        type_line="Creature — Fixture",
        keywords=(),
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=inner_residuals,
        effect_template=effect_template,
        trigger_effect_template=trigger_effect_template,
    )
    if inner is None or not inner.exact or inner_residuals:
        return None
    plan = attached_granted_ability_plan(
        node=inner,
        quoted_text=quoted,
        oracle_id=record.oracle_id,
        face_id=face_id,
        source_line=span.line,
        card_name="Granted creature",
        keywords=(),
    )
    if plan is None:
        return None
    compiled = attached_quoted_ability_handler(
        material_line,
        fragment=plan.fragment,
        fragment_capabilities=tuple(inner.capability_dependencies),
        source_name=source_name,
    )
    if compiled is None:
        return None
    outer = runtime_handler_node(
        node_id=node_id,
        line=line,
        span=span,
        compiled=compiled,
        kind="static_ability",
        event="characteristics.evaluate",
        runtime_coverage=("attached_typed_ability_grant",),
        dependency_reason=(
            "attached typed ability grant lacks trusted outer or inner "
            "capability closure"
        ),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )
    granted = replace(
        inner,
        node_id=f"{node_id}:granted",
        kind=plan.node_kind,
        text=quoted,
        active_zone="battlefield",
        residual_ids=(),
    )
    return outer, granted


def compile_keyword_or_attached_grant_nodes(
    *,
    record: CardRecord,
    face_id: str,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    source_name: str,
    effect_template: Any,
    keywords: Sequence[str],
    printed_card_types: tuple[str, ...],
    printed_subtypes: tuple[str, ...],
    saga_chapters: tuple[int, ...],
    printed_power: str | None,
    source_attachment_relation: AttachmentReferenceKind | None,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    keyword_node_compiler: Callable[..., Sequence[OracleNode]],
    compile_inner: Callable[..., OracleNode | None],
    grant_effect_templates: Callable[..., tuple[Any, Any]],
    material_line_for: Callable[[str], str],
) -> tuple[OracleNode, ...] | None:
    keyword_nodes = keyword_node_compiler(
        node_id=node_id, line=line, material_line=material_line,
        card_name=source_name, effect_template=effect_template,
        span=span, keywords=keywords,
        printed_card_types=printed_card_types, printed_subtypes=printed_subtypes,
        saga_chapters=saga_chapters, printed_power=printed_power,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals,
    )
    if keyword_nodes:
        return tuple(keyword_nodes)
    granted_effect, granted_trigger_effect = grant_effect_templates(
        True, ("creature",), None
    )
    return compile_attached_granted_ability_nodes(
        record=record, face_id=face_id, node_id=node_id, line=line,
        material_line=material_line, span=span, source_name=source_name,
        source_attachment_relation=source_attachment_relation,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals,
        compile_inner=compile_inner,
        effect_template=granted_effect,
        trigger_effect_template=granted_trigger_effect,
        material_line_for=material_line_for,
    )


__all__ = [
    "AttachedGrantedAbilityPlan",
    "GRANTED_ABILITY_KINDS",
    "GRANTED_ACTIVATED_ABILITY_KIND",
    "GRANTED_MANA_ABILITY_KIND",
    "GRANTED_TRIGGERED_ABILITY_KIND",
    "attached_granted_ability_plan",
    "attached_granted_program_ability_id",
    "compile_attached_granted_ability_nodes",
    "compile_keyword_or_attached_grant_nodes",
]
