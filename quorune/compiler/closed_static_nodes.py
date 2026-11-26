from __future__ import annotations

from typing import Iterable
from collections.abc import Mapping
from . import regex_working_set as re

from ..characteristic_evaluation import type_parts
from ..rules.capabilities import CapabilityRegistry
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.match_outcome_restrictions import MATCH_OUTCOME_RESTRICTION_TEMPLATE, compile_match_outcome_component
from .declaration_nodes import (
    declaration_static_node,
    fixed_static_declaration_grant_handler,
)
from .intrinsic_counter_nodes import intrinsic_counter_prohibition_node
from .maximum_hand_size_nodes import no_maximum_hand_size_node
from .player_target_protection_nodes import player_target_protection_node
from .defender_permission_templates import static_defender_permission_handler
from ..defender_permission import DEFENDER_DECLARATION_EVENT
from .as_unblocked_templates import static_as_unblocked_handler
from .toughness_assignment_templates import static_toughness_assignment_handler
from .block_capacity_templates import static_block_capacity_handler
from .landwalk_permission_templates import static_landwalk_permission_handler
from .block_as_though_templates import static_block_as_though_handler
from .ir_model import OracleNode, OracleResidual, SourceSpan
from .oracle_source_text import without_ability_word_label
from .kicker_nodes import fixed_kicked_entry_node
from .permanent_additional_cost_nodes import permanent_additional_cost_node
from .public_alternative_cost_nodes import (
    fixed_public_alternative_cost_static_node,
)
from .static_runtime_nodes import runtime_handler_node, static_runtime_node


def closed_static_card_node(*, record, node_id, line, material_line, span,
        face_name, type_line, permanent_card_types, capability_registry, capability_profile, residuals):
    """Bind a printed card face before dispatching ordinary static semantics.

    Synthetic token abilities use the record-free dispatcher below and cannot
    acquire a printed additional-cost instance from their creating card.
    """
    from .battlefield_activation_grant_nodes import battlefield_activation_grant_node

    grant = battlefield_activation_grant_node(source_name=face_name or record.name, node_id=node_id,
        line=line, material_line=material_line, span=span, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)
    if grant is not None:
        return grant
    from .boast_allowance_nodes import boast_allowance_node
    if allowance := boast_allowance_node(node_id=node_id, line=line, material_line=material_line, span=span,
            capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals):
        return allowance
    from .bargain_nodes import bargain_reduction_node
    reduction=bargain_reduction_node(record=record,node_id=node_id,line=line,material_line=material_line,
        span=span,capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    if reduction is not None:return reduction
    from .target_count_cost_nodes import target_count_cost_static_node
    target_price=target_count_cost_static_node(record=record,node_id=node_id,line=line,material_line=material_line,
        span=span,capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    if target_price is not None:return target_price
    from .splice_nodes import splice_static_node
    splice = splice_static_node(record=record, node_id=node_id, line=line, material_line=material_line,
        span=span, capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
    if splice is not None:
        return splice
    from .preparation_nodes import preparation_entry_node
    preparation=preparation_entry_node(record=record,face_name=face_name,node_id=node_id,line=line,material_line=material_line,
        span=span,capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    if preparation is not None:return preparation
    from .replicate_nodes import replicate_static_node
    replicate = replicate_static_node(record=record, node_id=node_id, line=line,
        material_line=material_line, span=span, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)
    if replicate is not None:
        return replicate
    from .disturb_nodes import fixed_face_pair_cast_static_node
    face_cast = fixed_face_pair_cast_static_node(record=record, face_name=face_name, node_id=node_id, line=line,
        material_line=material_line, span=span, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)
    if face_cast is not None:
        return face_cast
    from .opponent_discard_entry_nodes import opponent_discard_entry_node
    if discard_entry := opponent_discard_entry_node(type_line=type_line, node_id=node_id, line=line,
            material_line=material_line, span=span, capability_registry=capability_registry,
            capability_profile=capability_profile, residuals=residuals):
        return discard_entry
    from .self_graveyard_exile_nodes import self_graveyard_exile_node
    source_exile = self_graveyard_exile_node(source_name=face_name or record.name,
        node_id=node_id, line=line, material_line=material_line, span=span,
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
    if source_exile is not None:
        return source_exile
    from .multikicker_nodes import multikicker_static_node
    multikicker = multikicker_static_node(record=record, node_id=node_id, line=line,
        material_line=material_line, span=span, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)
    if multikicker is not None:
        return multikicker
    from .emerge_nodes import emerge_static_node
    emerge = emerge_static_node(record=record, node_id=node_id, line=line,
        material_line=material_line, span=span, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)
    if emerge is not None:
        return emerge
    from .paid_graveyard_nodes import paid_graveyard_node
    graveyard = paid_graveyard_node(record=record, node_id=node_id, line=line,
        material_line=material_line, span=span, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)
    if graveyard is not None:
        return graveyard
    from .conditional_recipients import conditional_recipient_node
    recipients = conditional_recipient_node(node_id=node_id, line=line, material_line=material_line,
        span=span, source_name=face_name or record.name, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)
    if recipients is not None:
        return recipients
    card_types, subtypes, _supertypes = type_parts(type_line)
    if "artifact" in card_types and "equipment" in subtypes:
        from .attached_combat_strike import equipped_combat_strike_template

        combat_strike = equipped_combat_strike_template(material_line)
        if combat_strike is not None:
            return runtime_handler_node(
                node_id=node_id, line=line, span=span, compiled=combat_strike,
                kind="static_ability", event="characteristics.evaluate",
                runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
                dependency_reason="Equipped first strike requires live attachment, block and subtype authorities.",
                capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
            )
    return closed_static_or_replacement_node(node_id=node_id, line=line, material_line=material_line,
        span=span, source_name=face_name or record.name, card_types=card_types,
        permanent_card_types=permanent_card_types, source_is_class='class' in subtypes,
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
        announced_x_available=not record.faces and record.mana_cost.count('{X}') == 1)


def closed_static_or_replacement_node(
    *, node_id: str, line: str, material_line: str, span: SourceSpan,
    source_name: str, card_types: Iterable[str], permanent_card_types: set[str],
    source_is_class: bool, capability_registry: CapabilityRegistry | None,
    capability_profile: str, residuals: list[OracleResidual], announced_x_available: bool = False,
) -> OracleNode | None:
    """Give exact owners their full source before generic label fallback."""
    material = without_ability_word_label(material_line)
    if material is None:return None
    outcome = compile_match_outcome_component(material)
    if outcome is not None and permanent_card_types.intersection(card_types):
        return runtime_handler_node(
            node_id=node_id, line=line, span=span,
            compiled=(MATCH_OUTCOME_RESTRICTION_TEMPLATE, outcome.descriptor(), outcome.dependencies),
            kind="static_ability", event="characteristics.evaluate",
            runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
            dependency_reason="Match outcome restrictions require their live source and outcome owner.",
            capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
        )
    arguments=dict(node_id=node_id,line=line,span=span,source_name=source_name,
        card_types=card_types,permanent_card_types=permanent_card_types,
        source_is_class=source_is_class,capability_registry=capability_registry,
        capability_profile=capability_profile,residuals=residuals,announced_x_available=announced_x_available)
    original=_closed_static_body_node(material_line=material_line,**arguments)
    if original is not None:return original
    if material==material_line:
        return None
    return _closed_static_body_node(material_line=material,**arguments)


def _closed_static_body_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    source_name: str,
    card_types: Iterable[str],
    permanent_card_types: set[str],
    source_is_class: bool,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    announced_x_available: bool = False,
) -> OracleNode | None:
    """Dispatch closed nontriggered static and replacement line owners."""

    from .classic_flash import classic_flash_node
    classic=classic_flash_node(node_id=node_id,line=line,material_line=material_line,span=span,
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    if classic is not None:return classic

    from .external_entry_counters import external_entry_counter_node
    counters=external_entry_counter_node(node_id=node_id,line=line,material_line=material_line,span=span,
        source_name=source_name,capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    if counters is not None:return counters

    from .target_restriction_nodes import target_restriction_node
    restriction=target_restriction_node(node_id=node_id,line=line,material_line=material_line,span=span,
        source_name=source_name,card_types=card_types,permanent_card_types=permanent_card_types,capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    if restriction is not None:return restriction

    from .private_entry_hand_templates import private_entry_hand_node
    if hand_entry := private_entry_hand_node(node_id=node_id, line=line, material_line=material_line, span=span,
        source_name=source_name, card_types=card_types, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals):
        return hand_entry

    from .linked_graveyard_entry_templates import entry_copy_or_linked_exile_node

    copied_entry = entry_copy_or_linked_exile_node(node_id=node_id, line=line, material_line=material_line,
        span=span, source_name=source_name, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals, announced_x_available=announced_x_available)
    if copied_entry is not None:
        return copied_entry

    if re.fullmatch(
        r".+? can't (?:attack or block|attack|block|be blocked) this turn\.?",
        material_line, re.I,
    ):
        return None

    casting_price = permanent_additional_cost_node(
        node_id=node_id, line=line, material_line=material_line, span=span,
        card_types=card_types, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals,
    )
    if casting_price is not None:
        return casting_price
    counter_prohibition = intrinsic_counter_prohibition_node(
        node_id=node_id,
        line=line,
        material_line=material_line,
        span=span,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )
    if counter_prohibition is not None:
        return counter_prohibition
    kicked_entry = fixed_kicked_entry_node(
        node_id=node_id,
        line=line,
        material_line=material_line,
        span=span,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )
    if kicked_entry is not None:
        return kicked_entry
    alternative_cost = fixed_public_alternative_cost_static_node(
        node_id=node_id,
        line=line,
        material_line=material_line,
        span=span,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )
    if alternative_cost is not None:
        return alternative_cost
    player_protection = player_target_protection_node(
        node_id=node_id,line=line,material_line=material_line,span=span,source_name=source_name,
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals,
    )
    if player_protection is not None:
        return player_protection
    no_maximum = no_maximum_hand_size_node(
        node_id=node_id,
        line=line,
        material_line=material_line,
        span=span,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )
    if no_maximum is not None:
        return no_maximum
    declaration_grant = static_block_as_though_handler(material_line,source_name=source_name) or static_landwalk_permission_handler(material_line,source_name=source_name) or static_block_capacity_handler(material_line,source_name=source_name) or static_toughness_assignment_handler(material_line,source_name=source_name) or static_as_unblocked_handler(material_line,source_name=source_name) or static_defender_permission_handler(material_line, source_name=source_name) or fixed_static_declaration_grant_handler(
        material_line,
        source_name=source_name,
    )
    if declaration_grant is not None:
        declaration_permission = (isinstance(declaration_grant[1], Mapping)
            and declaration_grant[1].get('event') == DEFENDER_DECLARATION_EVENT)
        return runtime_handler_node(
            node_id=node_id,
            line=line,
            span=span,
            compiled=declaration_grant,
            kind="static_ability",
            event=(DEFENDER_DECLARATION_EVENT if declaration_permission else "characteristics.evaluate"),
            runtime_coverage=("fixed_static_declaration_grant", *(
                ('current_ability_fragment_required',) if declaration_permission else ())),
            dependency_reason=(
                "queried declaration rules require their continuous ability "
                "grant and combat declaration capabilities"
            ),
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            residuals=residuals,
        )
    declaration = declaration_static_node(
        node_id=node_id,
        line=line,
        material_line=material_line,
        card_name=source_name,
        span=span,
        residuals=residuals,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    if declaration is not None:
        return declaration
    return static_runtime_node(
        node_id=node_id,
        line=line,
        material_line=material_line,
        span=span,
        source_name=source_name,
        card_types=card_types,
        permanent_card_types=permanent_card_types,
        source_is_class=source_is_class,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )


__all__ = ["closed_static_or_replacement_node"]
