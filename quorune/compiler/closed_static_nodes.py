from __future__ import annotations

from typing import Iterable
from collections.abc import Mapping
import re

from ..characteristic_evaluation import type_parts
from ..rules.capabilities import CapabilityRegistry
from .declaration_nodes import (
    declaration_static_node,
    fixed_static_declaration_grant_handler,
)
from .intrinsic_counter_nodes import intrinsic_counter_prohibition_node
from .maximum_hand_size_nodes import no_maximum_hand_size_node
from .player_target_protection_nodes import player_target_protection_node
from .defender_permission_templates import static_defender_permission_handler
from .as_unblocked_templates import static_as_unblocked_handler
from .toughness_assignment_templates import static_toughness_assignment_handler
from .block_capacity_templates import static_block_capacity_handler
from .landwalk_permission_templates import static_landwalk_permission_handler
from .ir_model import OracleNode, OracleResidual, SourceSpan
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
    from .disturb_nodes import disturb_static_node
    disturb = disturb_static_node(record=record, face_name=face_name, node_id=node_id, line=line,
        material_line=material_line, span=span, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)
    if disturb is not None:
        return disturb
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
    card_types, subtypes, _supertypes = type_parts(type_line)
    return closed_static_or_replacement_node(node_id=node_id, line=line, material_line=material_line,
        span=span, source_name=face_name or record.name, card_types=card_types,
        permanent_card_types=permanent_card_types, source_is_class='class' in subtypes,
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)


def closed_static_or_replacement_node(
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
) -> OracleNode | None:
    """Dispatch closed nontriggered static and replacement line owners."""

    from .classic_flash import classic_flash_node
    classic=classic_flash_node(node_id=node_id,line=line,material_line=material_line,span=span,
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    if classic is not None:return classic

    from .target_restriction_nodes import target_restriction_node
    restriction=target_restriction_node(node_id=node_id,line=line,material_line=material_line,span=span,
        source_name=source_name,card_types=card_types,permanent_card_types=permanent_card_types,capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    if restriction is not None:return restriction

    from .entry_reveal_templates import entry_reveal_node
    revealed_entry=entry_reveal_node(node_id=node_id,line=line,material_line=material_line,
        span=span,source_name=source_name,capability_registry=capability_registry,
        capability_profile=capability_profile,residuals=residuals)
    if revealed_entry is not None:return revealed_entry

    from .entry_copy_templates import entry_copy_node
    copied_entry = entry_copy_node(node_id=node_id, line=line, material_line=material_line,
        span=span, source_name=source_name, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals)
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
        node_id=node_id,line=line,material_line=material_line,span=span,
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
    declaration_grant = static_landwalk_permission_handler(material_line,source_name=source_name) or static_block_capacity_handler(material_line,source_name=source_name) or static_toughness_assignment_handler(material_line,source_name=source_name) or static_as_unblocked_handler(material_line,source_name=source_name) or static_defender_permission_handler(material_line, source_name=source_name) or fixed_static_declaration_grant_handler(
        material_line,
        source_name=source_name,
    )
    if declaration_grant is not None:
        numeric_permission = (isinstance(declaration_grant[1], Mapping)
            and declaration_grant[1].get('handler_id') == 'permission.attack.defender-current-numeric.v1')
        return runtime_handler_node(
            node_id=node_id,
            line=line,
            span=span,
            compiled=declaration_grant,
            kind="static_ability",
            event=(declaration_grant[1]['event'] if numeric_permission else "characteristics.evaluate"),
            runtime_coverage=("fixed_static_declaration_grant", *(
                ('current_ability_fragment_required',) if numeric_permission else ())),
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
