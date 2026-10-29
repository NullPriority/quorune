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
from ..mana_restrictions import valid_mana_spend_restriction
from ..granted_activation_model import RESTRICTED_MANA_GRANT_CAPABILITY
from ..carddb import CardRecord
from ..rules.capabilities import CapabilityRegistry
from .continuous_templates import (
    attached_quoted_ability_handler,
    attached_quoted_ability_text,
    fixed_query_quoted_ability_handler,
    fixed_query_quoted_ability_text,
)
from .conditional_granted_ability_templates import (
    conditional_quoted_ability_text,
    conditional_quoted_ability_handler,
)
from .public_query_effect_amounts import (
    _source_characteristic_amount_base,
)
from .granted_program_bounds import granted_effect_values_are_closed
from .granted_static_costs import compile_static_cost_or_ordinary_inner
from .granted_characteristic_nodes import granted_characteristic_reference_is_closed
from .ir_model import OracleNode, OracleResidual, SourceSpan
from .static_runtime_nodes import runtime_handler_node


GRANTED_ACTIVATED_ABILITY_KIND = "granted_activated_ability"
GRANTED_MANA_ABILITY_KIND = "granted_mana_ability"
GRANTED_TRIGGERED_ABILITY_KIND = "granted_triggered_ability"
GRANTED_STATIC_ABILITY_KIND = 'granted_static_ability'
GRANTED_ABILITY_KINDS = frozenset(
    {
        GRANTED_ACTIVATED_ABILITY_KIND,
        GRANTED_MANA_ABILITY_KIND,
        GRANTED_TRIGGERED_ABILITY_KIND,
        GRANTED_STATIC_ABILITY_KIND,
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
    capability_dependencies: tuple[str, ...] = ()


def attached_granted_program_ability_id(
    *,
    kind: str,
    face_id: str,
    line: int,
    node_id: str | None = None,
) -> str | None:
    token_slot = ""
    token_match = re.search(
        r":token-ability:(?P<slot>[1-9][0-9]*)$",
        str(node_id or ""),
    )
    if token_match is not None:
        token_slot = f":token:{token_match.group('slot')}"
    quote_match = re.search(r':quoted-ability:(?P<slot>[1-9][0-9]*)(?::granted)?$',str(node_id or ''))
    if quote_match is not None:
        token_slot = f":quote:{quote_match.group('slot')}"
    if kind in {
        GRANTED_ACTIVATED_ABILITY_KIND,
        GRANTED_MANA_ABILITY_KIND,
    }:
        return f"ability:granted:{face_id}:n{line}{token_slot}"
    if kind == GRANTED_TRIGGERED_ABILITY_KIND:
        return f"trigger:{face_id}:n{line}:granted{token_slot}"
    if kind == GRANTED_STATIC_ABILITY_KIND:
        return f'static:granted:{face_id}:n{line}{token_slot}'
    return None


def _closed_granted_activation(ability: ActivatedAbility) -> bool:
    """Keep grants inside the shared closed granted-activation schema."""

    from ..granted_activation_model import normalize_granted_cost_choices
    try:
        normalize_granted_cost_choices(tuple(choice.to_dict() for choice in ability.choices))
    except ValueError:
        return False
    return bool(
        ability.zones == ("battlefield",)
        and ability.compiled_cost
        and not ability.complex_symbols
        and not ability.discard_source
        and not ability.exile_source
        and ability.energy_payment == 0
        and ability.random_discard_payment == 0
        and ability.loyalty_delta is None
        and ability.source_counter_removal_cost is None
        and not ability.uncompiled_costs
        and ability.generic_reduction_per_legendary_creature == 0
        and (ability.crew_threshold is None or type(ability.crew_threshold) is int and ability.crew_threshold>=0)
        and ability.color_set_mana_output is None
        and ability.activation_limit is None
        and not ability.library_search_types
        and not ability.activation_conditions
        and ability.dynamic_mana_output is None
        and (ability.mana_spend_restriction is None or (
            valid_mana_spend_restriction(ability.mana_spend_restriction)
            and ability.mana_spend_restriction != 'legendary_spell_uncounterable'))
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
    node_id: str | None = None,
    display_text: str | None = None,
    allow_static_cost: bool = False,
) -> AttachedGrantedAbilityPlan | None:
    """Build one closed typed grant from an independently exact inner node."""

    if (
        not node.exact
        or node.residual_ids
        or not granted_effect_values_are_closed(node.effects)
    ):
        return None
    if allow_static_cost and node.event == 'cast.cost.modify':
        from .granted_static_costs import static_cost_granted_plan
        return static_cost_granted_plan(node=node, oracle_id=oracle_id, face_id=face_id,
            source_line=source_line, node_id=node_id)
    if allow_static_cost and node.event == 'characteristics.evaluate':
        from .granted_characteristic_nodes import characteristic_granted_plan
        return characteristic_granted_plan(node=node, oracle_id=oracle_id, face_id=face_id,
            source_line=source_line, node_id=node_id)
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
            node_id=node_id,
        )
        if ability_id is None:
            return None
        semantic_key = f"{oracle_id}:{ability_id}"
        extended_cost = bool(
            ability.untap_source
            or ability.sacrifice_source
            or ability.life_payment
            or ability.choices
            or ability.mana_spend_restriction is not None
        )
        restricted_grant = ability.mana_spend_restriction not in {None, 'nonartifact_spell_prohibited'}
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
                untap_source=ability.untap_source,
                sacrifice_source=ability.sacrifice_source,
                life_payment=ability.life_payment,
                choices=tuple(choice.to_dict() for choice in ability.choices),
                mana_spend_restriction=ability.mana_spend_restriction,
                crew_threshold=ability.crew_threshold,
                schema_version=4 if restricted_grant else 3 if ability.crew_threshold is not None else 2 if extended_cost else 1,
            )
        )
        return AttachedGrantedAbilityPlan(
            node_kind=node_kind,
            ability_id=ability_id,
            semantic_key=semantic_key,
            fragment=fragment,
            capability_dependencies=(RESTRICTED_MANA_GRANT_CAPABILITY,) if restricted_grant else (),
        )
    if node.kind != "triggered_ability" or not node.event:
        return None
    node_kind = GRANTED_TRIGGERED_ABILITY_KIND
    ability_id = attached_granted_program_ability_id(
        kind=node_kind,
        face_id=face_id,
        line=source_line,
        node_id=node_id,
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
                label=display_text or quoted_text,
            )
        ),
    )


def _compile_granted_ability_pair(
    *,
    record: CardRecord,
    face_id: str,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    quoted: str,
    compile_shell: Callable[
        [Mapping[str, Any], tuple[str, ...]],
        tuple[str, Mapping[str, Any], tuple[str, ...]] | None,
    ],
    runtime_coverage: str,
    dependency_reason: str,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    compile_inner: Callable[..., OracleNode | None],
    effect_template: Any,
    trigger_effect_template: Any,
    material_line_for: Callable[[str], str],
) -> tuple[OracleNode, OracleNode] | None:
    """Compile one accepted grant shell and one independent inner ability."""

    inner_residuals: list[OracleResidual] = []
    inner = compile_static_cost_or_ordinary_inner(compile_inner,
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
    source_characteristics = _source_characteristic_amount_base(inner.effects)
    if source_characteristics is not None and inner.target_schema is None:
        from ..rules.capabilities import capability_dependencies_for_node
        template_id, effects, target_schema, mechanics = source_characteristics.compiled()
        mechanics = tuple(dict.fromkeys((*inner.mechanics, *mechanics)))
        dependencies = tuple(sorted(set(inner.capability_dependencies) | set(capability_dependencies_for_node(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
            cost_schema=inner.cost,
        ))))
        if not dependencies or capability_registry is None:
            return None
        closure = capability_registry.closure(dependencies, profile=capability_profile)
        if not closure.trusted:
            return None
        inner = replace(
            inner, template_id=template_id, effects=effects, target_schema=target_schema,
            mechanics=mechanics, capability_dependencies=dependencies,
            capability_closure=closure.reachable, capability_profile=closure.profile,
            capability_fingerprint=closure.fingerprint,
        )
    plan = attached_granted_ability_plan(
        node=inner,
        quoted_text=quoted,
        oracle_id=record.oracle_id,
        face_id=face_id,
        source_line=span.line,
        node_id=node_id if ':quoted-ability:' in node_id else None,
        card_name="Granted creature",
        keywords=(),
        allow_static_cost=True,
    )
    if plan is None:
        return None
    from .granted_static_costs import bind_granted_plan_dependencies
    inner = bind_granted_plan_dependencies(inner, plan, capability_registry, capability_profile)
    if inner is None:
        return None
    compiled = compile_shell(
        plan.fragment,
        tuple(inner.capability_dependencies),
    )
    if compiled is None:
        return None
    from ..continuous_conditions import GRAVEYARD_QUERY_GRANT_HANDLER_ID,GRAVEYARD_CONDITIONAL_GRANT_HANDLER_ID
    outer = runtime_handler_node(
        node_id=node_id,
        line=line,
        span=span,
        compiled=compiled,
        kind="static_ability",
        event="characteristics.evaluate",
        active_zone='graveyard' if compiled[1].get('handler_id') in {
            GRAVEYARD_QUERY_GRANT_HANDLER_ID,GRAVEYARD_CONDITIONAL_GRANT_HANDLER_ID} else 'battlefield',
        runtime_coverage=(runtime_coverage,),
        dependency_reason=dependency_reason,
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

    from .soulbond_templates import paired_quoted_text, paired_quoted_handler
    paired = paired_quoted_text(material_line, source_name=source_name)
    if paired is None and source_attachment_relation not in {
        AttachmentReferenceKind.ENCHANTED,
        AttachmentReferenceKind.EQUIPPED,
    }:
        return None
    quoted = paired or attached_quoted_ability_text(
        material_line,
        source_name=source_name,
    )
    if quoted is None or (_EXTERNAL_ATTACHMENT_REFERENCE.search(quoted) and not granted_characteristic_reference_is_closed(quoted)):
        return None
    normalized_name = source_name.casefold().strip()
    if normalized_name and normalized_name in quoted.casefold():
        return None
    return _compile_granted_ability_pair(
        record=record,
        face_id=face_id,
        node_id=node_id,
        line=line,
        material_line=material_line,
        span=span,
        quoted=quoted,
        compile_shell=lambda fragment, fragment_capabilities: (
            (paired_quoted_handler if paired is not None else attached_quoted_ability_handler)(
                material_line,
                fragment=fragment,
                fragment_capabilities=fragment_capabilities,
                source_name=source_name,
            )
        ),
        runtime_coverage="attached_typed_ability_grant",
        dependency_reason=(
            "attached typed ability grant lacks trusted outer or inner "
            "capability closure"
        ),
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
        compile_inner=compile_inner,
        effect_template=effect_template,
        trigger_effect_template=trigger_effect_template,
        material_line_for=material_line_for,
    )


def compile_fixed_query_granted_ability_nodes(
    *,
    record: CardRecord,
    face_id: str,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    source_name: str,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    compile_inner: Callable[..., OracleNode | None],
    effect_template: Any,
    trigger_effect_template: Any,
    material_line_for: Callable[[str], str],
) -> tuple[OracleNode, OracleNode] | None:
    """Compile a typed quoted ability granted to one closed live query."""

    from .granted_keyword_event_nodes import compile_granted_keyword_event_nodes
    keyword_grant = compile_granted_keyword_event_nodes(record=record, face_id=face_id,
        node_id=node_id, line=line, material_line=material_line, span=span, source_name=source_name,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals, effect_template=effect_template,
        trigger_effect_template=trigger_effect_template, material_line_for=material_line_for)
    if keyword_grant is not None:
        return keyword_grant
    quoted = fixed_query_quoted_ability_text(material_line)
    if quoted is None or (_EXTERNAL_ATTACHMENT_REFERENCE.search(quoted) and not granted_characteristic_reference_is_closed(quoted)):
        return None
    normalized_name = source_name.casefold().strip()
    if normalized_name and normalized_name in quoted.casefold():
        return None
    return _compile_granted_ability_pair(
        record=record,
        face_id=face_id,
        node_id=node_id,
        line=line,
        material_line=material_line,
        span=span,
        quoted=quoted,
        compile_shell=lambda fragment, fragment_capabilities: (
            fixed_query_quoted_ability_handler(
                material_line,
                fragment=fragment,
                fragment_capabilities=fragment_capabilities,
            )
        ),
        runtime_coverage="fixed_query_typed_ability_grant",
        dependency_reason=(
            "fixed query typed ability grant lacks trusted outer or inner "
            "capability closure"
        ),
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
        compile_inner=compile_inner,
        effect_template=effect_template,
        trigger_effect_template=trigger_effect_template,
        material_line_for=material_line_for,
    )


def _ordinary_granted_inner(compile_inner, trigger_node):
    """Use the same ordinary-source fallback as a standalone ability."""
    def compile_complete(**arguments):
        result = compile_inner(**arguments)
        if result is not None or arguments['residuals']:
            return result
        trigger_arguments = {key:value for key,value in arguments.items()
            if key not in {'type_line','keywords','trigger_effect_template'}}
        trigger_arguments['effect_template'] = arguments.get('trigger_effect_template') or arguments['effect_template']
        return trigger_node(**trigger_arguments)
    return compile_complete


def _source_bound_cost_nodes(*, record, node_id, line, material_line, span,
        source_name, effect_template, trusted_mechanics, capability_registry,
        capability_profile, residuals):
    """Route complete source cost and cost-result families before ordinary nodes."""
    from .reconfigure_nodes import reconfigure_nodes
    reconfigure = reconfigure_nodes(record=record, node_id=node_id, line=line,
        material_line=material_line, span=span, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)
    if reconfigure is not None:
        return reconfigure
    from .multikicker_results import multikicker_result_node
    counted = multikicker_result_node(record=record, node_id=node_id, line=line,
        material_line=material_line, span=span, effect_template=effect_template,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)
    if counted is not None:
        return (counted,)
    from .mana_color_result_nodes import mana_color_result_node
    spent = mana_color_result_node(record=record, node_id=node_id, line=line,
        material_line=material_line, span=span, effect_template=effect_template,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)
    if spent is not None:
        return (spent,)
    from .conditional_flash_nodes import conditional_self_flash_nodes
    flash = conditional_self_flash_nodes(node_id=node_id, line=line, material_line=material_line,
        span=span, source_name=source_name, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)
    if flash is not None:
        return flash
    from .self_cast_restriction_nodes import self_cast_restriction_nodes
    return self_cast_restriction_nodes(node_id=node_id, line=line, material_line=material_line,
        span=span, source_name=source_name, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)


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
    trigger_node: Callable[..., OracleNode | None],
    grant_effect_templates: Callable[..., tuple[Any, Any]],
    material_line_for: Callable[[str], str],
) -> tuple[OracleNode, ...] | None:
    from .enchanted_player_statics import enchanted_player_static_node
    player_scoped=enchanted_player_static_node(node_id=node_id,line=line,material_line=material_line,span=span,
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    if player_scoped is not None:return (player_scoped,)
    source_cost = _source_bound_cost_nodes(record=record, node_id=node_id, line=line,
        material_line=material_line, span=span, source_name=source_name, effect_template=effect_template,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)
    if source_cost is not None:
        return source_cost
    from .role_token_nodes import role_token_nodes
    role = role_token_nodes(record=record, face_id=face_id, node_id=node_id, line=line,
        material_line=material_line, span=span, source_name=source_name, effect_template=effect_template,
        keywords=keywords, printed_card_types=printed_card_types, source_attachment_relation=source_attachment_relation,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, compile_inner=compile_inner, trigger_node=trigger_node,
        grant_effect_templates=grant_effect_templates)
    if role is not None:
        return role
    from .typed_token_ability_nodes import typed_token_ability_nodes

    from .exert_nodes import exert_attack_nodes
    exerted=exert_attack_nodes(record=record,face_id=face_id,node_id=node_id,line=line,
        material_line=material_line,span=span,source_name=source_name,
        effect_template=grant_effect_templates(True,printed_card_types,source_attachment_relation)[1],
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    if exerted is not None:
        return exerted

    from .max_speed_nodes import max_speed_nodes
    max_result=max_speed_nodes(record=record,face_id=face_id,node_id=node_id,line=line,material_line=material_line,span=span,source_name=source_name,keywords=keywords,printed_card_types=printed_card_types,
        trusted_mechanics=trusted_mechanics,capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals,compile_inner=compile_inner,
        effect_template=effect_template,trigger_effect_template=grant_effect_templates(True,printed_card_types,source_attachment_relation)[1],material_line_for=material_line_for)
    if max_result is not None:return max_result
    typed_token_nodes = typed_token_ability_nodes(
        record=record,
        face_id=face_id,
        node_id=node_id,
        line=line,
        material_line=material_line,
        span=span,
        source_name=source_name,
        effect_template=effect_template,
        keywords=keywords,
        printed_card_types=printed_card_types,
        source_attachment_relation=source_attachment_relation,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        compile_inner=compile_inner,
        trigger_node=trigger_node,
        grant_effect_templates=grant_effect_templates,
    )
    if typed_token_nodes is not None:
        return typed_token_nodes
    from .attached_untap_restraints import attached_untap_restraint_node
    restraint=attached_untap_restraint_node(node_id=node_id,line=line,material_line=material_line,span=span,
        source_name=source_name,capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    if restraint is not None:return (restraint,)
    keyword_nodes = keyword_node_compiler(
        record=record, face_id=face_id,
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
    compile_inner = _ordinary_granted_inner(compile_inner, trigger_node)
    return _compile_quoted_grant_forms(record=record,face_id=face_id,node_id=node_id,line=line,
        material_line=material_line,span=span,source_name=source_name,printed_card_types=printed_card_types,
        source_attachment_relation=source_attachment_relation,trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals,
        compile_inner=compile_inner,trigger_node=trigger_node,keyword_node_compiler=keyword_node_compiler,
        effect_template=granted_effect,trigger_effect_template=granted_trigger_effect,material_line_for=material_line_for)


def _compile_quoted_grant_forms(*,record,face_id,node_id,line,material_line,span,source_name,
        printed_card_types,source_attachment_relation,trusted_mechanics,capability_registry,
        capability_profile,residuals,compile_inner,trigger_node,keyword_node_compiler,
        effect_template,trigger_effect_template,material_line_for):
    """Route complete quoted children after ordinary keywords and source costs."""
    from .entry_copy_ability_nodes import entry_copy_ability_nodes
    copied = entry_copy_ability_nodes(record=record,face_id=face_id,node_id=node_id,line=line,
        material_line=material_line,span=span,source_name=source_name,trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,capability_profile=capability_profile,compile_inner=compile_inner,
        effect_template=effect_template,trigger_effect_template=trigger_effect_template,material_line_for=material_line_for,keyword_node_compiler=keyword_node_compiler)
    if copied is not None:return copied
    from .temporary_quoted_ability_nodes import temporary_quoted_ability_nodes
    temporary = temporary_quoted_ability_nodes(record=record, face_id=face_id,
        node_id=node_id, line=line, material_line=material_line, span=span,
        source_name=source_name, printed_card_types=printed_card_types,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals, compile_inner=compile_inner,
        effect_template=effect_template, trigger_effect_template=trigger_effect_template,
        trigger_node=trigger_node, material_line_for=material_line_for)
    if temporary is not None:
        return temporary
    from .composite_grant_nodes import composite_granted_ability_nodes
    composite=composite_granted_ability_nodes(record=record,face_id=face_id,node_id=node_id,
        line=line,material_line=material_line,span=span,source_name=source_name,
        trusted_mechanics=trusted_mechanics,capability_registry=capability_registry,
        capability_profile=capability_profile,residuals=residuals,compile_inner=compile_inner,
        effect_template=effect_template,trigger_effect_template=trigger_effect_template,material_line_for=material_line_for)
    if composite is not None:return composite
    quoted = conditional_quoted_ability_text(material_line, source_name=source_name)
    if quoted is not None and (not _EXTERNAL_ATTACHMENT_REFERENCE.search(quoted) or granted_characteristic_reference_is_closed(quoted)):
        conditional = _compile_granted_ability_pair(
            record=record, face_id=face_id, node_id=node_id, line=line,
            material_line=material_line, span=span, quoted=quoted,
            compile_shell=lambda fragment, fragment_capabilities: conditional_quoted_ability_handler(
                material_line, source_name=source_name, fragment=fragment,
                fragment_capabilities=fragment_capabilities),
            runtime_coverage="conditional_typed_ability_grant",
            dependency_reason="conditional typed grant requires outer and inner capability closure",
            trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
            capability_profile=capability_profile, residuals=residuals,
            compile_inner=compile_inner, effect_template=effect_template,
            trigger_effect_template=trigger_effect_template, material_line_for=material_line_for,
        )
        if conditional is not None:
            return conditional
    fixed_query_grant = compile_fixed_query_granted_ability_nodes(
        record=record, face_id=face_id, node_id=node_id, line=line,
        material_line=material_line, span=span, source_name=source_name,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals,
        compile_inner=compile_inner,
        effect_template=effect_template,
        trigger_effect_template=trigger_effect_template,
        material_line_for=material_line_for,
    )
    if fixed_query_grant is not None:
        return fixed_query_grant
    return compile_attached_granted_ability_nodes(
        record=record, face_id=face_id, node_id=node_id, line=line,
        material_line=material_line, span=span, source_name=source_name,
        source_attachment_relation=source_attachment_relation,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals,
        compile_inner=compile_inner,
        effect_template=effect_template,
        trigger_effect_template=trigger_effect_template,
        material_line_for=material_line_for,
    )


__all__ = [
    "AttachedGrantedAbilityPlan",
    "GRANTED_ABILITY_KINDS",
    "GRANTED_ACTIVATED_ABILITY_KIND",
    "GRANTED_MANA_ABILITY_KIND",
    "GRANTED_TRIGGERED_ABILITY_KIND",
    "GRANTED_STATIC_ABILITY_KIND",
    "attached_granted_ability_plan",
    "attached_granted_program_ability_id",
    "compile_attached_granted_ability_nodes",
    "compile_fixed_query_granted_ability_nodes",
    "compile_keyword_or_attached_grant_nodes",
]
