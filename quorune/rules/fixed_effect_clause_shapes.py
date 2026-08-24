from __future__ import annotations

"""Capability closure for generic two-clause effect sequences."""

from typing import Any, Iterable, Mapping, Sequence

from .affected_player_sacrifice_capability_shapes import (
    fixed_affected_player_sacrifice_node_capabilities,
)
from .affected_player_discard_capability_shapes import (
    fixed_affected_player_discard_node_capabilities,
)
from .counter_capability_shapes import (
    fixed_counter_placement_group_node_capabilities,
)
from .counter_removal_capabilities import (
    all_counter_removal_node_capabilities,
    fixed_counter_removal_node_capabilities,
)
from .fixed_controller_effect_shapes import fixed_life_node_capabilities
from .fixed_resolution_characteristic_shapes import (
    fixed_controlled_characteristic_set_node_capabilities,
)
from .graveyard_card_targets import (
    targeted_own_graveyard_return_node_capabilities,
)
from .node_capability_shapes import (
    fixed_amass_node_capabilities,
    fixed_bolster_node_capabilities,
    fixed_counter_placement_batch_node_capabilities,
    fixed_counter_placement_node_capabilities,
    fixed_counter_placement_set_node_capabilities,
    fixed_counter_placement_target_set_node_capabilities,
    fixed_damage_node_capabilities,
    fixed_draw_node_capabilities,
    fixed_player_counter_placement_node_capabilities,
    fixed_scry_node_capabilities,
    fixed_self_counter_keyword_action_node_capabilities,
    fixed_target_characteristics_node_capabilities,
    mass_destruction_node_capabilities,
    self_regeneration_node_capabilities,
    single_explore_node_capabilities,
    single_proliferate_node_capabilities,
    targeted_counter_node_capabilities,
    targeted_destruction_node_capabilities,
    targeted_exile_node_capabilities,
    targeted_return_to_hand_node_capabilities,
    targeted_tap_state_node_capabilities,
)
from .library_search_capability_shapes import (
    fixed_library_search_node_capabilities,
    fixed_type_to_hand_search_node_capabilities,
)
from .mill_capability_shapes import fixed_mill_node_capabilities
from .public_zone_move_capability_shapes import (
    fixed_public_zone_move_set_node_capabilities,
    public_graveyard_card_exile_node_capabilities,
)
from .token_creation_capability_shapes import (
    fixed_token_creation_node_capabilities,
)
from .surveil_capability_shapes import fixed_surveil_node_capabilities


FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC = "fixed-effect-clause-sequence"
FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY = (
    "resolution.effect_sequence.fixed_clauses"
)

_COMPONENT_RESOLVERS = (
    all_counter_removal_node_capabilities,
    fixed_affected_player_discard_node_capabilities,
    fixed_affected_player_sacrifice_node_capabilities,
    fixed_counter_placement_batch_node_capabilities,
    fixed_counter_placement_group_node_capabilities,
    fixed_counter_placement_node_capabilities,
    fixed_counter_placement_set_node_capabilities,
    fixed_counter_placement_target_set_node_capabilities,
    fixed_counter_removal_node_capabilities,
    fixed_player_counter_placement_node_capabilities,
    fixed_target_characteristics_node_capabilities,
    fixed_damage_node_capabilities,
    fixed_controlled_characteristic_set_node_capabilities,
    mass_destruction_node_capabilities,
    fixed_draw_node_capabilities,
    fixed_life_node_capabilities,
    fixed_scry_node_capabilities,
    fixed_surveil_node_capabilities,
    fixed_mill_node_capabilities,
    fixed_library_search_node_capabilities,
    fixed_type_to_hand_search_node_capabilities,
    single_explore_node_capabilities,
    single_proliferate_node_capabilities,
    self_regeneration_node_capabilities,
    fixed_self_counter_keyword_action_node_capabilities,
    fixed_bolster_node_capabilities,
    fixed_amass_node_capabilities,
    targeted_counter_node_capabilities,
    targeted_destruction_node_capabilities,
    targeted_exile_node_capabilities,
    targeted_own_graveyard_return_node_capabilities,
    targeted_return_to_hand_node_capabilities,
    targeted_tap_state_node_capabilities,
    public_graveyard_card_exile_node_capabilities,
    fixed_public_zone_move_set_node_capabilities,
    fixed_token_creation_node_capabilities,
)


def _contains_target_reference(value: Any) -> bool:
    if isinstance(value, str):
        return (
            value in {"$target", "$targets"}
            or value.startswith("$target.")
        )
    if isinstance(value, Mapping):
        return any(_contains_target_reference(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_target_reference(child) for child in value)
    return False


def closed_effect_component_capabilities(
    effects: Sequence[Mapping[str, Any]],
    *,
    target_schema: Mapping[str, Any] | None,
    mechanics: set[str],
) -> tuple[str, ...]:
    dependencies: set[str] = set()
    for resolver in _COMPONENT_RESOLVERS:
        component_mechanics = mechanics
        if resolver is fixed_token_creation_node_capabilities:
            characteristics = (
                effects[0].get("characteristics")
                if len(effects) == 1
                else None
            )
            keywords = (
                characteristics.get("keywords", ())
                if isinstance(characteristics, Mapping)
                else ()
            )
            component_mechanics = {
                "cr-111-tokens",
                *(
                    str(keyword).casefold()
                    for keyword in keywords
                    if isinstance(keyword, str)
                ),
            }
        elif resolver is self_regeneration_node_capabilities:
            component_mechanics = {"regenerate"}
        dependencies.update(
            resolver(
                effects=effects,
                target_schema=target_schema,
                mechanic_ids=component_mechanics,
            )
        )
    return tuple(sorted(dependencies))


def fixed_effect_clause_sequence_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Own two independently closed effects in their printed order."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC not in mechanics
        or len(effects) != 2
    ):
        return ()
    target_components = tuple(
        index
        for index, effect in enumerate(effects)
        if _contains_target_reference(effect)
    )
    if target_schema is None:
        if target_components:
            return ()
    elif len(target_components) != 1:
        return ()

    dependencies = {FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY}
    for index, effect in enumerate(effects):
        component = closed_effect_component_capabilities(
            (effect,),
            target_schema=(
                target_schema if index in target_components else None
            ),
            mechanics=mechanics - {FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC},
        )
        if not component:
            return ()
        dependencies.update(component)
    return tuple(sorted(dependencies))


__all__ = [
    "FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY",
    "FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC",
    "closed_effect_component_capabilities",
    "fixed_effect_clause_sequence_node_capabilities",
]
