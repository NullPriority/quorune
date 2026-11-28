from __future__ import annotations

"""Cardinality authority projects results to their unchanged fixed-set owners."""

from collections.abc import Mapping
from copy import deepcopy

from ..compiler.any_number_target_sets import ANY_NUMBER_TARGET_MECHANIC
from ..library_order_model import GRAVEYARD_LIBRARY_ORDER_MECHANIC
from ..target_cardinality import (
    ANY_NUMBER_TARGET_CAPABILITY, AnnouncedTargetCardinality,
    SELECTED_PLAYER_RESULT_CAPABILITY, SELECTED_PLAYER_RESULT_LAYOUTS, SELECTED_PLAYER_RESULT_MECHANIC,
    SELECTED_PLAYER_SACRIFICE_MANA_DRAW_LAYOUT,
    bind_any_number_target_schema, validate_selected_target_count,
)


def _selected_query_amount_dependencies(value):
    from ..query_effect_amount_model import (
        CONTROLLER_MAXIMUM_PERMANENT_MANA_AMOUNT, PUBLIC_QUERY_AMOUNT_CAPABILITY, PublicQueryAmountSpec,
    )

    try:
        amount = PublicQueryAmountSpec.from_dict(value)
    except (TypeError, ValueError):
        return ()
    return (PUBLIC_QUERY_AMOUNT_CAPABILITY,) if amount == CONTROLLER_MAXIMUM_PERMANENT_MANA_AMOUNT else ()


def _selected_player_leaf_capabilities(effect):
    from .affected_player_discard_capability_shapes import fixed_affected_player_discard_node_capabilities
    from .fixed_controller_effect_shapes import fixed_life_node_capabilities
    from .mill_capability_shapes import fixed_mill_node_capabilities
    from .node_capability_shapes import fixed_draw_node_capabilities
    from .whole_hand_discard_shapes import whole_hand_discard_node_capabilities

    if effect.get("players") != "$targets":
        return ()
    normalized = {key: value for key, value in effect.items() if key != "players"}
    schema = {"zones": ["player"], "categories": ["player"], "count": 1}
    mechanics = {"cr-115-targets"}
    amount_dependencies = ()
    op = effect.get("op")
    if op == "mill" and set(normalized) == {"op", "count"}:
        normalized["player"] = "$target.0"
        resolver = fixed_mill_node_capabilities
        mechanics.add("mill")
        count = effect.get("count")
        if isinstance(count, Mapping):
            from ..selected_graveyard_mill_count import (
                SELECTED_GRAVEYARD_MILL_CAPABILITY, SELECTED_GRAVEYARD_MILL_KIND, SelectedGraveyardMillCount,
            )

            if count.get("kind") == SELECTED_GRAVEYARD_MILL_KIND:
                SelectedGraveyardMillCount.from_dict(count)
                amount_dependencies = (SELECTED_GRAVEYARD_MILL_CAPABILITY,)
                normalized["count"] = 1
            else:
                mechanics.add("half-current-library-mill")
    elif op in {"life", "lose_life"} and set(normalized) == {"op", "delta" if op == "life" else "amount"}:
        normalized["player"] = "$target.0"
        schema["player_relation"] = "any"
        resolver = fixed_life_node_capabilities
        mechanics.add("cr-119-life")
    elif op == "choose_cards_apnap" and effect.get("then") == "sacrifice":
        from ..object_predicate import ObjectQuerySpec
        from .affected_player_sacrifice_capability_shapes import fixed_affected_player_sacrifice_node_capabilities

        creature = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))
        if "target" in effect or effect.get("count") != 1 or effect.get("predicate") != creature.to_dict():
            return ()
        normalized.update(players=["$target.0"], target="$target.0")
        schema["player_relation"] = "any"
        resolver = fixed_affected_player_sacrifice_node_capabilities
        mechanics.update(("fixed-affected-player-sacrifice", "sacrifice"))
    elif op == "choose_cards_apnap" and set(normalized) == {
        "op", "actor", "zone", "predicate", "count", "then", "prompt", "hidden",
    }:
        normalized.update(players=["$target.0"], target="$target.0")
        schema["player_relation"] = "any"
        resolver = fixed_affected_player_discard_node_capabilities
        mechanics.update(("fixed-affected-player-discard", "cr-402-hand"))
    elif op == "discard_whole_hands" and set(normalized) == {"op", "actor"}:
        normalized["players"] = ["$target.0"]
        schema["player_relation"] = "any"
        resolver = whole_hand_discard_node_capabilities
        mechanics.update(("whole-hand-discard", "cr-402-hand"))
    elif op == "draw_each_player" and set(normalized) == {"op", "count"}:
        schema = None
        resolver = fixed_draw_node_capabilities
        mechanics.add("cr-121-drawing-a-card")
    else:
        return ()
    amount_field = {"life": "delta", "choose_cards_apnap": "count"}.get(op)
    if isinstance(normalized.get(amount_field), Mapping):
        amount_dependencies = _selected_query_amount_dependencies(normalized[amount_field])
        if not amount_dependencies:
            return ()
        normalized[amount_field] = 1
    required = resolver(effects=(normalized,), target_schema=schema, mechanic_ids=mechanics)
    return (*required, *amount_dependencies) if required else ()


def selected_player_payload_dependencies(effect):
    if effect.get("players") != "$targets":
        return ()
    dependencies = _selected_player_leaf_capabilities(effect)
    if not dependencies:
        raise ValueError("Selected player result has an unowned payload")
    return (*dependencies, SELECTED_PLAYER_RESULT_CAPABILITY, ANY_NUMBER_TARGET_CAPABILITY,
            "target.revalidate_resolution")


def selected_player_result_node_capabilities(*, effects, target_schema, mechanic_ids):
    if not {SELECTED_PLAYER_RESULT_MECHANIC, ANY_NUMBER_TARGET_MECHANIC} <= set(mechanic_ids):
        return ()
    if not isinstance(target_schema, Mapping) or set(target_schema) != {
        "zones", "categories", "player_relation", "count_parameter",
    }:
        return ()
    if (target_schema["zones"] != ["player"] or target_schema["categories"] != ["player"]
            or target_schema["player_relation"] not in {"any", "opponent"}):
        return ()
    try:
        if AnnouncedTargetCardinality.from_dict(target_schema["count_parameter"]).parameter != "any_number":
            return ()
        operations = tuple(effect.get("op") for effect in effects)
        if operations not in SELECTED_PLAYER_RESULT_LAYOUTS:
            return ()
        if operations == SELECTED_PLAYER_SACRIFICE_MANA_DRAW_LAYOUT:
            life, sacrifice, mana, draw = effects
            if (target_schema["player_relation"] != "any" or life.get("amount") != 2
                    or sacrifice.get("then") != "sacrifice" or mana.get("color") != "B"
                    or mana.get("amount") != 2 or "spend_restriction" in mana
                    or draw.get("player") != "$controller" or draw.get("count") != 1):
                return ()
        elif any(effect.get("op") == "choose_cards_apnap" and effect.get("then") == "sacrifice"
                 for effect in effects):
            return ()
        dependencies = set()
        for effect in effects:
            if effect.get("op") == "destroy_all":
                from .destruction_regeneration_capability_shapes import mass_destruction_node_capabilities
                owned = mass_destruction_node_capabilities(
                    effects=(effect,), target_schema=None, mechanic_ids=set(mechanic_ids) - {"cr-115-targets"},
                )
            elif effect.get("op") == "mana":
                from .fixed_resolution_mana_shapes import fixed_resolution_mana_capabilities
                owned = fixed_resolution_mana_capabilities(effects=(effect,), target_schema=None, mechanic_ids=mechanic_ids)
            elif effect.get("op") == "draw":
                from .node_capability_shapes import fixed_draw_node_capabilities
                owned = fixed_draw_node_capabilities(effects=(effect,), target_schema=None, mechanic_ids=mechanic_ids)
            else:
                owned = selected_player_payload_dependencies(effect)
            if not owned:
                return ()
            dependencies.update(owned)
    except (TypeError, ValueError):
        return ()
    return tuple(sorted(dependencies))


def any_number_target_node_capabilities(*, effects, target_schema, mechanic_ids):
    from .airbend import TARGETED_FIELD, airbend_node_capabilities

    if any(TARGETED_FIELD in effect for effect in effects):
        return airbend_node_capabilities(effects, target_schema, None)
    from .announced_destruction_controller_tokens import (
        announced_destruction_controller_token_dependencies, uses_announced_destruction_controller_tokens,
    )

    if len(effects) == 1 and isinstance(effects[0].get("copy_batch"), Mapping):
        from ..token_copy_recipes import TokenCopyBatchKind, TokenCopyBatchSpec
        from ..replacement.immutable import thaw_value

        try:
            spec = TokenCopyBatchSpec.from_effect(effects[0])
        except (TypeError, ValueError):
            return ()
        if spec.kind is TokenCopyBatchKind.ANNOUNCED_HASTY_COPIES and ANY_NUMBER_TARGET_MECHANIC in mechanic_ids:
            return spec.capability_dependencies if spec.target_schema == thaw_value(target_schema) else ()
    if len(effects) == 1 and uses_announced_destruction_controller_tokens(effects[0]):
        if ANY_NUMBER_TARGET_MECHANIC not in mechanic_ids:
            return ()
        try:
            return announced_destruction_controller_token_dependencies(effects[0], target_schema)
        except (TypeError, ValueError):
            return ()
    from .targeted_control_set import TARGETED_CONTROL_SET_VERSION, targeted_control_set_node_capabilities

    if len(effects) == 1 and TARGETED_CONTROL_SET_VERSION in effects[0]:
        return targeted_control_set_node_capabilities(effects=effects, target_schema=target_schema)
    from .temporary_copy_subject import TEMPORARY_COPY_SUBJECT_OPERATION, temporary_copy_subject_node_capabilities

    if len(effects) == 1 and effects[0].get("op") == TEMPORARY_COPY_SUBJECT_OPERATION:
        return temporary_copy_subject_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        )
    from ..phasing_model import PHASE_OUT_OPERATION
    if len(effects) == 1 and effects[0].get("op") == PHASE_OUT_OPERATION and effects[0].get("schema_version") == 3:
        from .occurrence_object_results import phase_out_node_capabilities
        return phase_out_node_capabilities(effects, target_schema)
    from ..public_zone_moves import STACK_SPELL_EXILE_FIELD, stack_spell_exile_node_capabilities

    if any(STACK_SPELL_EXILE_FIELD in effect for effect in effects):
        if target_schema is None:
            return ()
        return stack_spell_exile_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        )
    from ..resolution_color_model import TARGET_COLOR_SET_VERSION,TARGET_COLOR_SCOPE_MECHANIC
    if any(e.get('schema_version')==TARGET_COLOR_SET_VERSION and e.get('op')=='apply_source_characteristics_until_end_of_turn' for e in effects):
        from .resolution_colors import resolution_color_node_capabilities
        return resolution_color_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=(TARGET_COLOR_SCOPE_MECHANIC,))
    if SELECTED_PLAYER_RESULT_MECHANIC in mechanic_ids:
        return selected_player_result_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids)
    if GRAVEYARD_LIBRARY_ORDER_MECHANIC in mechanic_ids:
        from .delayed_draw_capability_shapes import fixed_next_turn_draw_node_capabilities
        from .library_order_shapes import fixed_library_order_capabilities
        from .node_capability_shapes import fixed_draw_node_capabilities

        owned = fixed_library_order_capabilities(
            effects=effects[:1], target_schema=target_schema, mechanic_ids=mechanic_ids,
        )
        if ANY_NUMBER_TARGET_CAPABILITY not in owned or len(effects) > 2:
            return ()
        if len(effects) == 1:
            return owned
        followup = dict(effects=effects[1:], target_schema=None, mechanic_ids=mechanic_ids)
        draw = fixed_draw_node_capabilities(**followup) or fixed_next_turn_draw_node_capabilities(**followup)
        return (*owned, *draw) if draw else ()
    if isinstance(target_schema,Mapping) and {'distinct_controllers','retain_controller'}.intersection(target_schema):
        from .per_player_target_sets import per_player_target_node_capabilities
        return per_player_target_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
    if ANY_NUMBER_TARGET_MECHANIC not in mechanic_ids or not isinstance(target_schema, Mapping):
        return ()
    try:
        spec = AnnouncedTargetCardinality.from_dict(target_schema["count_parameter"])
        if spec.parameter != "any_number" or len(effects) != 1:
            return ()
        effect = deepcopy(dict(effects[0]))
        validate_selected_target_count(effect["maximum_targets"])
        if effect.pop("announced_target_count") is not True:
            return ()
        effect["maximum_targets"] = 2
        schema = bind_any_number_target_schema(target_schema, 2)
    except (KeyError, TypeError, ValueError):
        return ()
    from .fixed_homogeneous_target_set_capability_shapes import fixed_homogeneous_target_set_node_capabilities
    from .target_characteristic_sets import target_characteristic_set_capabilities
    from .counter_target_set_shapes import quantified_counter_target_capabilities
    from .all_kind_counter_shapes import all_kind_counter_node_capabilities
    from .power_toughness_switch import power_toughness_switch_node_capabilities
    for owner in (fixed_homogeneous_target_set_node_capabilities, target_characteristic_set_capabilities,
            quantified_counter_target_capabilities, all_kind_counter_node_capabilities, power_toughness_switch_node_capabilities):
        required = owner(effects=(effect,), target_schema=schema, mechanic_ids=mechanic_ids)
        if required:
            from .capabilities import capability_dependencies_for_node
            ambient=capability_dependencies_for_node(effects=(effect,),target_schema=schema,
                mechanic_ids=set(mechanic_ids)-{ANY_NUMBER_TARGET_MECHANIC})
            return tuple(sorted({*required, *ambient, ANY_NUMBER_TARGET_CAPABILITY}))
    return ()


def is_closed_any_number_target_program(program):
    required = any_number_target_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)
