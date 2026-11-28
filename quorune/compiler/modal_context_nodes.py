from __future__ import annotations

"""Composition owner for typed modal trigger and activation blocks."""

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Sequence

from ..event_counter_reference import attached_attack_counter_context_matches
from .event_counter_results import entry_event_counter_result
from .ir_model import OracleNode, OracleResidual, SourceSpan
from .modal_templates import fixed_nonrepeating_modal_template
from .captured_counter_keyword_mode import captured_counter_keyword_mode


_MODAL_BODY_SENTINEL = "__quorune_fixed_nonrepeating_modal_body__"
NodeCompiler = Callable[..., OracleNode | None]


@dataclass(frozen=True, slots=True)
class FixedModalContextBlocks:
    """Precompiled modal rows with source-order residual insertion."""

    nodes: Mapping[int, tuple[tuple[OracleNode, ...], tuple[OracleResidual, ...]]]
    consumed_rows: frozenset[int]

    def append_to(
        self,
        row_index: int,
        *,
        nodes: list[OracleNode],
        residuals: list[OracleResidual],
    ) -> bool:
        if row_index in self.consumed_rows:
            return True
        result = self.nodes.get(row_index)
        if result is None:
            return False
        (node, *granted_children), block_residuals = result
        residual_offset = len(residuals)
        remapped_ids = tuple(
            f"r{residual_offset + int(residual_id[1:])}"
            for residual_id in node.residual_ids
        )
        residuals.extend(
            replace(
                residual,
                residual_id=f"r{residual_offset + index}",
            )
            for index, residual in enumerate(block_residuals, 1)
        )
        nodes.append(replace(node, residual_ids=remapped_ids))
        nodes.extend(granted_children)
        return True


def _modal_block_end(
    material_rows: Sequence[tuple[str, str, SourceSpan]],
    start: int,
) -> int:
    end = start + 1
    while (
        end < len(material_rows)
        and material_rows[end][1].startswith("• ")
    ):
        end += 1
    return end


def _equipped_attack_binding(header: str, *, card_name: str):
    if not header.casefold().startswith(("whenever equipped creature attacks,", "when equipped creature attacks,")):
        return None
    from .fixed_counter_trigger_nodes import fixed_counter_trigger_binding

    binding = fixed_counter_trigger_binding(header, card_name=card_name)
    if binding is None or not attached_attack_counter_context_matches(
        event=binding.event.value, active_zone=binding.active_zone,
        event_condition=binding.event_condition,
    ):
        return None
    return binding


def _controlled_combat_recipient_branch(header, body):
    if not header.casefold().startswith("whenever a creature you control deals combat damage to a player, choose one "):
        return None
    from ..combat_damage_library_exile import combat_damage_color_cast_exile_effect
    from ..goad_instruction import GOAD_MECHANIC
    from ..rules.combat_damage_library_access import combat_damage_recipient_goad
    from .impulse_access_templates import IMPULSE_ACCESS_MECHANIC_ID

    printed = " ".join(body.casefold().replace("\u2019", "'").split())
    if printed == "goad target creature that player controls.":
        effect, target = combat_damage_recipient_goad()
        return "combat-damage-recipient-goad-v1", (effect,), target, (GOAD_MECHANIC, "cr-115-targets")
    if printed == (
        "exile the top card of that player's library. until end of turn, you may cast that card "
        "and you may spend mana as though it were mana of any color to cast that spell."
    ):
        return ("combat-damage-recipient-color-cast-v1", (combat_damage_color_cast_exile_effect(),),
                None, (IMPULSE_ACCESS_MECHANIC_ID,))
    return None


def _bind_combat_recipient_modes(node, target_schema):
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..rules.combat_damage_library_access import (
        combat_damage_recipient_modal_schema_is_exact, controlled_combat_damage_player_condition,
        uses_combat_damage_recipient_modes,
    )

    if node is None or not uses_combat_damage_recipient_modes(target_schema):
        return node
    condition = controlled_combat_damage_player_condition(node.event_condition)
    if (node.event != "damage.dealt" or node.active_zone != "battlefield"
            or condition is None or not combat_damage_recipient_modal_schema_is_exact(target_schema)):
        return None
    return replace(
        node, event_condition=condition,
        runtime_coverage=tuple(dict.fromkeys((*node.runtime_coverage, CURRENT_ABILITY_FRAGMENT_COVERAGE))),
    )


def _context_node(
    *,
    node_id: str,
    oracle_id: str,
    face_id: str,
    rows: Sequence[tuple[str, str, SourceSpan]],
    oracle_text: str,
    card_name: str,
    type_line: str,
    keywords: Sequence[str],
    trusted_mechanics: frozenset[str],
    capability_registry: Any,
    capability_profile: str,
    effect_template: Any,
    activated_or_event_node: NodeCompiler,
    trigger_node: NodeCompiler,
) -> tuple[tuple[OracleNode, ...], tuple[OracleResidual, ...]] | None:
    from .temporary_quoted_ability_nodes import temporary_land_mana_mode_nodes

    binding = _equipped_attack_binding(rows[0][1], card_name=card_name)
    mode_rows = iter(rows[1:])
    granted_children: list[OracleNode] = []

    def compile_mode(body: str):
        line, _material, span = next(mode_rows)
        if combat := _controlled_combat_recipient_branch(rows[0][1], body):
            return combat
        ordinary = effect_template(body, card_name=card_name)
        if ordinary[0] is not None:
            return ordinary
        captured = captured_counter_keyword_mode(rows[0][1], body, card_name=card_name)
        if captured is not None:
            return captured
        if binding is not None:
            counter = entry_event_counter_result(
                binding, body, effect_template=effect_template, card_name=card_name,
            )
            if counter is not None:
                return counter[0]
        grant = temporary_land_mana_mode_nodes(body=body, line=line, span=span, oracle_id=oracle_id,
            face_id=face_id, compile_inner=activated_or_event_node, effect_template=effect_template,
            trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
            capability_profile=capability_profile)
        if grant is None:
            return ordinary
        compiled, child = grant
        granted_children.append(child)
        return compiled

    modal = fixed_nonrepeating_modal_template(
        rows,
        compile_effect=compile_mode,
    )
    if modal is None or not modal.context_prefix:
        return None
    template, effects, target_schema, mechanics = modal.compiled()

    def modal_effect_template(
        body: str,
        **kwargs: Any,
    ) -> tuple[
        str | None,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        if body == _MODAL_BODY_SENTINEL:
            return template, effects, target_schema, mechanics
        return effect_template(body, **kwargs)

    synthetic_line = f"{modal.context_prefix}{_MODAL_BODY_SENTINEL}"
    span = SourceSpan(rows[0][2].start, rows[-1][2].end, rows[0][2].line)
    scratch_residuals: list[OracleResidual] = []
    node = activated_or_event_node(
        node_id=node_id,
        line=synthetic_line,
        material_line=synthetic_line,
        span=span,
        card_name=card_name,
        type_line=type_line,
        keywords=keywords,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=scratch_residuals,
        effect_template=modal_effect_template,
    )
    if node is None:
        node = trigger_node(
            node_id=node_id,
            line=synthetic_line,
            span=span,
            card_name=card_name,
            trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            residuals=scratch_residuals,
            effect_template=modal_effect_template,
        )
    node = _bind_combat_recipient_modes(node, target_schema)
    if node is None or not node.lowerable:
        return None
    if modal.minimum_modes == 0:
        from ..rules.modal_selection import optional_combat_trigger_context_matches

        if not optional_combat_trigger_context_matches(
            event=node.event, active_zone=node.active_zone, event_condition=node.event_condition,
        ):
            return None
    from ..rules.modal_selection import distinct_turn_trigger_context_matches, has_distinct_turn_trigger_modes

    if has_distinct_turn_trigger_modes(target_schema) and not distinct_turn_trigger_context_matches(
        event=node.event, active_zone=node.active_zone, event_condition=node.event_condition,
    ):
        return None
    block_text = oracle_text[span.start : span.end]
    return (
        (replace(node, text=block_text, span=span), *granted_children),
        tuple(
            replace(residual, text=block_text, span=span)
            for residual in scratch_residuals
        ),
    )


def fixed_nonrepeating_modal_context_blocks(
    *,
    material_rows: Sequence[tuple[str, str, SourceSpan]],
    oracle_id: str,
    face_id: str,
    oracle_text: str,
    card_name: str,
    type_line: str,
    keywords: Sequence[str],
    trusted_mechanics: frozenset[str],
    capability_registry: Any,
    capability_profile: str,
    effect_template: Any,
    activated_or_event_node: NodeCompiler,
    trigger_node: NodeCompiler,
) -> FixedModalContextBlocks:
    """Compile every disjoint header-plus-bullet modal context block."""

    nodes: dict[int, tuple[tuple[OracleNode, ...], tuple[OracleResidual, ...]]] = {}
    consumed_rows: set[int] = set()
    for start in range(len(material_rows)):
        if start in consumed_rows:
            continue
        end = _modal_block_end(material_rows, start)
        if end - start < 3:
            continue
        result = _context_node(
            node_id=f"{face_id}:n{start + 1}",
            oracle_id=oracle_id,
            face_id=face_id,
            rows=material_rows[start:end],
            oracle_text=oracle_text,
            card_name=card_name,
            type_line=type_line,
            keywords=keywords,
            trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            effect_template=effect_template,
            activated_or_event_node=activated_or_event_node,
            trigger_node=trigger_node,
        )
        if result is None:
            continue
        nodes[start] = result
        consumed_rows.update(range(start + 1, end))
    return FixedModalContextBlocks(nodes, frozenset(consumed_rows))


__all__ = [
    "FixedModalContextBlocks",
    "fixed_nonrepeating_modal_context_blocks",
]
