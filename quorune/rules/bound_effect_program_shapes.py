from __future__ import annotations

"""Closure of flattened effects sharing an already established reference."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.bound_effect_programs import (
    BOUND_EFFECT_PROGRAM_CAPABILITY, BOUND_EFFECT_PROGRAM_MECHANIC, CLOSED_EFFECT_PROGRAM_MECHANIC,
    target_controller_prefix_matches, target_controller_search_prefix_matches,
    target_controller_zone_result_prefix_matches,
)
from ..compiler.optional_effect_templates import OPTIONAL_EFFECT_OPERATION
from ..replacement.immutable import thaw_value
from ..target_controller_information import (
    TARGET_CONTROLLER_INFORMATION_CAPABILITY, TARGET_CONTROLLER_INFORMATION_KIND,
    TARGET_CONTROLLER_INFORMATION_MECHANIC, controller_reference_index,
)
from .damage_capability_shapes import fixed_damage_node_capabilities
from .fixed_effect_clause_shapes import closed_effect_component_capabilities, _contains_target_reference
from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed


def _target_zero_only(value: Any) -> bool:
    if isinstance(value, str) and value.startswith("$target"):
        return value in {"$target.0", "$target.current_controller.0"}
    if isinstance(value, Mapping):
        return all(_target_zero_only(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return all(_target_zero_only(child) for child in value)
    return True


def has_target_controller_recipient(effect: Mapping[str, Any]) -> bool:
    from .target_recipient_results import contains_target_recipient_information
    if contains_target_recipient_information(effect):return True
    if not isinstance(effect, Mapping):
        return False
    fields = ("target",) if effect.get("op") == OPTIONAL_EFFECT_OPERATION else ("target", "player")
    return any(isinstance(effect.get(field), Mapping) and effect[field].get("kind") == TARGET_CONTROLLER_INFORMATION_KIND
               for field in fields)


def _target_controller_player_result_dependencies(effects, schema, mechanics) -> tuple[str, ...]:
    result = effects[1]
    if (
        TARGET_CONTROLLER_INFORMATION_MECHANIC not in mechanics
        or not target_controller_zone_result_prefix_matches(effects[0])
        or result.get("op") not in {"draw", "life", "lose_life"}
        or any(_contains_target_reference(effect) or has_target_controller_recipient(effect)
               or has_target_controller_search_role(effect) for effect in effects[2:])
    ):
        return ()
    try:
        if controller_reference_index(result.get("player")) != 0:
            return ()
    except ValueError:
        return ()
    restored = {**result, "player": "$controller"}
    components = (
        closed_effect_component_capabilities(effects[:1], target_schema=schema, mechanics=mechanics),
        closed_effect_component_capabilities((restored,), target_schema=None, mechanics=mechanics),
        *(closed_effect_component_capabilities((effect,), target_schema=None, mechanics=mechanics)
          for effect in effects[2:]),
    )
    if not all(components):
        return ()
    return (TARGET_CONTROLLER_INFORMATION_CAPABILITY, *(dependency for part in components for dependency in part))


def _target_controller_pair_dependencies(effects, schema, mechanics) -> tuple[str, ...]:
    if (
        CLOSED_EFFECT_PROGRAM_MECHANIC not in mechanics
        or schema is None or schema.get("up_to") or not direct_permanent_target_schema_is_closed(schema)
        or not has_target_controller_recipient(effects[1]) or not _target_zero_only(effects[0])
    ):
        return ()
    if "player" in effects[1]:
        return _target_controller_player_result_dependencies(effects, schema, mechanics)
    if len(effects) != 2 or not target_controller_prefix_matches(effects[0]):
        return ()
    prefix = closed_effect_component_capabilities(
        effects[:1], target_schema=schema, mechanics=mechanics,
    )
    damage = fixed_damage_node_capabilities(
        effects=effects[1:], target_schema=None, mechanic_ids=mechanics,
    )
    return (*prefix, *damage) if prefix and damage else ()


def has_target_controller_search_role(effect: Mapping[str, Any]) -> bool:
    if not isinstance(effect, Mapping) or effect.get("op") != OPTIONAL_EFFECT_OPERATION:
        return False
    nested = effect.get("effects")
    search_player = (
        nested[0].get("searching_player")
        if isinstance(nested, (list, tuple)) and len(nested) == 1 and isinstance(nested[0], Mapping)
        else None
    )
    return any(isinstance(role, Mapping) and role.get("kind") == TARGET_CONTROLLER_INFORMATION_KIND
               for role in (effect.get("player"), search_player))


def _target_controller_search_dependencies(effects, schema, mechanics) -> tuple[str, ...]:
    if (
        not {CLOSED_EFFECT_PROGRAM_MECHANIC, TARGET_CONTROLLER_INFORMATION_MECHANIC} <= mechanics
        or schema is None or schema.get("up_to") or not direct_permanent_target_schema_is_closed(schema)
        or not target_controller_search_prefix_matches(effects[0]) or not _target_zero_only(effects[0])
        or any(_contains_target_reference(effect) or has_target_controller_recipient(effect)
               or has_target_controller_search_role(effect) for effect in effects[2:])
    ):
        return ()
    wrapper = effects[1]
    nested = wrapper.get("effects")
    if (
        set(wrapper) != {"op", "player", "effects"} or wrapper["op"] != OPTIONAL_EFFECT_OPERATION
        or not isinstance(nested, (list, tuple)) or len(nested) != 1
        or not isinstance(nested[0], Mapping) or nested[0].get("op") != "search"
    ):
        return ()
    search = dict(nested[0])
    try:
        if controller_reference_index(wrapper["player"]) != 0 or controller_reference_index(search.pop("searching_player")) != 0:
            return ()
    except (KeyError, ValueError):
        return ()
    if search.get("destination") != "battlefield" or search.get("count") != {"minimum": 1, "maximum": 1}:
        return ()
    restored = {**wrapper, "player": "$controller", "effects": [search]}
    parts = (
        closed_effect_component_capabilities(effects[:1], target_schema=schema, mechanics=mechanics),
        closed_effect_component_capabilities((restored,), target_schema=None, mechanics=mechanics),
        *(closed_effect_component_capabilities((effect,), target_schema=None, mechanics=mechanics)
          for effect in effects[2:]),
    )
    if not all(parts):
        return ()
    return (TARGET_CONTROLLER_INFORMATION_CAPABILITY, *(dependency for part in parts for dependency in part))


def _untap_and_combat_prevention_dependencies(effects, schema, mechanics):
    if (len(effects) != 3 or CLOSED_EFFECT_PROGRAM_MECHANIC not in mechanics
            or effects[0] != {"op": "untap", "card": "$target.0"}):
        return ()

    from ..compiler.direct_target import DirectPermanentTargetSpec
    from .prevention_component_shapes import paired_target_combat_prevention_capabilities

    targets = (
        DirectPermanentTargetSpec(types_any=("creature",), combat_state="attacking"),
        DirectPermanentTargetSpec(types_any=("creature",), controller_relation="you", combat_state="attacking"),
        DirectPermanentTargetSpec(types_all=("creature",), keywords_all=("shadow",), combat_state="attacking"),
    )
    if thaw_value(schema) not in tuple(target.to_target_schema() for target in targets):
        return ()
    untap = closed_effect_component_capabilities(effects[:1], target_schema=schema, mechanics=mechanics)
    prevention = paired_target_combat_prevention_capabilities(
        effects=effects[1:], target_schema=schema, mechanic_ids=mechanics,
    )
    return (*untap, *prevention) if untap and prevention else ()


def _source_target_counter_batch_node_capabilities(effects, target_schema, mechanics):
    from ..counter_placement_sets import (
        SOURCE_TARGET_COUNTER_BATCH_CAPABILITY, source_target_counter_batch_projection,
    )
    from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
    from ..replacement.immutable import FrozenMap
    from .node_capability_shapes import fixed_counter_placement_batch_node_capabilities

    recipient = {"zones": ["battlefield"], "categories": ["permanent"], "count": 1, "types_any": ["creature"]}
    if (BOUND_EFFECT_PROGRAM_MECHANIC not in mechanics or len(effects) != 2
            or not isinstance(target_schema, Mapping)
            or FrozenMap(target_schema) != FrozenMap({**recipient, "source_exclusion": True})
            or dict(effects[0]) != {"op": "grant_keyword_until_end_of_turn", "card": "$target.0",
                                    "keyword": "Indestructible"}):
        return ()
    try:
        batch = source_target_counter_batch_projection(effects[1])
    except (TypeError, KeyError, ValueError):
        return ()
    grant = closed_effect_component_capabilities(effects[:1], target_schema=target_schema, mechanics=mechanics)
    counters = fixed_counter_placement_batch_node_capabilities(
        effects=(batch,), target_schema=recipient, mechanic_ids=mechanics,
    )
    if not grant or not counters:
        return ()
    return tuple(sorted({BOUND_EFFECT_PROGRAM_CAPABILITY, "resolution.effect_program.closed_components",
                         SOURCE_TARGET_COUNTER_BATCH_CAPABILITY, *grant, *counters,
                         *FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES["Indestructible"],
                         *FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES["Lifelink"]}))


def _source_target_counter_batch_cost_capabilities(program):
    from ..compiler.activated_costs import activated_ability_cost, activated_ability_cost_capabilities
    from ..replacement.immutable import FrozenMap
    from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors
    from .activation_counter_cost import SourceCounterRemovalCost

    ability, = activated_abilities_from_descriptors(program.handlers)
    expected_price = FrozenMap({
        "text": ability.cost_text,
        "mana": {key: int(key == "GENERIC") for key in ("B", "C", "G", "GENERIC", "R", "U", "W")},
        "complex_symbols": [],
        "tap_source": False,
        "untap_source": False,
        "discard_source": False,
        "sacrifice_source": False,
        "exile_source": False,
        "life_payment": 0,
        "energy_payment": 0,
        "loyalty_delta": None,
        "source_counter_removal_cost": SourceCounterRemovalCost("indestructible", 1).to_dict(),
        "choices": [],
        "uncompiled_costs": [],
    })
    if (ability.zones != ("battlefield",) or ability.mana_ability or ability.sorcery_speed
            or ability.activation_conditions or ability.generic_reduction_per_legendary_creature
            or program.ability_id != f"ability:{ability.ability_id}" or ability.builtin_semantic_key != program.key
            or ability.target_schema != FrozenMap(program.target_schema)
            or FrozenMap(activated_ability_cost(ability)) != expected_price
            or FrozenMap(program.cost_schema or {}) != expected_price):
        raise ValueError("The paired placements require one generic mana and one source indestructible counter")
    return activated_ability_cost_capabilities(ability)


def source_target_counter_batch_program_dependencies(program):
    from ..counter_placement_sets import SOURCE_TARGET_COUNTER_BATCH_CAPABILITY, uses_source_target_counter_batch
    from ..semantic_runtime.activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID
    from .effect_shape_walkers import nested_effects

    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if uses_source_target_counter_batch(effect))
    if not marked and SOURCE_TARGET_COUNTER_BATCH_CAPABILITY not in program.capability_dependencies:
        return ()
    if (marked != tuple(program.effects[1:]) or (program.event, program.active_zone) != ("activate", "battlefield")
            or len(program.handlers) != 1 or program.event_condition is not None
            or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID):
        raise ValueError("The paired placements require their original paid battlefield ability")
    dependencies = _source_target_counter_batch_node_capabilities(program.effects, program.target_schema, set(program.coverage))
    if not dependencies:
        raise ValueError("The two-subject batch requires its complete original target grant and counters")
    return (*dependencies, *_source_target_counter_batch_cost_capabilities(program))


def bound_effect_program_node_capabilities(
    *, effects: Sequence[Mapping[str, Any]], target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = set(mechanic_ids)
    from ..counter_placement_sets import uses_source_target_counter_batch
    from .target_recipient_results import target_recipient_result_node_capabilities
    from .qualified_token_creators import uses_qualified_token_creators, qualified_token_creator_node_capabilities
    from .third_player_rounds import is_third_player_round_choice, third_player_rounds_node_capabilities

    if any(uses_source_target_counter_batch(effect) for effect in effects):
        return _source_target_counter_batch_node_capabilities(effects, target_schema, mechanics)
    if any(uses_qualified_token_creators(effect) for effect in effects):
        return qualified_token_creator_node_capabilities(effects, target_schema, mechanics)

    if any(is_third_player_round_choice(effect) for effect in effects):
        return third_player_rounds_node_capabilities(effects, target_schema, mechanics)
    from ..compiler.target_recipient_results import TARGET_RECIPIENT_RESULT_MECHANIC
    if TARGET_RECIPIENT_RESULT_MECHANIC in mechanics:
        return target_recipient_result_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if (BOUND_EFFECT_PROGRAM_MECHANIC not in mechanics or not 2 <= len(effects) <= 8
        or any(not isinstance(effect, Mapping) for effect in effects)):
        return ()
    component_schema = target_schema
    legacy_attachment = False
    if target_schema is not None:
        if type(target_schema.get("count")) is not int or target_schema.get("count") != 1:
            return ()
        player_schema = {
            "zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1,
        }
        if dict(target_schema) not in (player_schema, {**player_schema, "player_relation": "opponent"}):
            if not direct_permanent_target_schema_is_closed(target_schema):
                legacy = {"zones": ["battlefield"], "categories": ["permanent"],
                          "creature": True, "controller": "you", "count": 1}
                if dict(target_schema) != legacy or target_schema.get("creature") is not True:
                    return ()
                from .attachment_action_capability_shapes import fixed_attachment_action_node_capabilities
                if (effects[0].get("attachment_kind") != "equipment"
                        or not fixed_attachment_action_node_capabilities(
                            effects=effects[:1], target_schema=target_schema, mechanic_ids=mechanics)):
                    return ()
                from ..compiler.direct_target import DirectPermanentTargetSpec
                component_schema = DirectPermanentTargetSpec(
                    types_any=("creature",), controller_relation="you").to_target_schema()
                legacy_attachment = True
    target_seen = False
    dependencies = {BOUND_EFFECT_PROGRAM_CAPABILITY, "resolution.effect_program.closed_components"}
    untap_prevention = _untap_and_combat_prevention_dependencies(effects, target_schema, mechanics)
    if untap_prevention:
        return tuple(sorted(dependencies.union(untap_prevention)))
    if len(effects) == 2 and effects[0].get("op") == "damage":
        from .temporary_keyword_removal import damage_then_fixed_keyword_removal_capabilities
        damage_removal = damage_then_fixed_keyword_removal_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
        )
        if damage_removal:
            return tuple(sorted(dependencies.union(damage_removal)))
    if has_target_controller_search_role(effects[1]):
        search = _target_controller_search_dependencies(effects, target_schema, mechanics)
        return tuple(sorted(dependencies.union(search))) if search else ()
    if any(has_target_controller_recipient(effect) for effect in effects):
        pair = _target_controller_pair_dependencies(effects, target_schema, mechanics)
        return tuple(sorted(dependencies.union(pair))) if pair else ()
    for index, effect in enumerate(effects):
        if not isinstance(effect, Mapping) or not _target_zero_only(effect):
            return ()
        targeted = _contains_target_reference(effect)
        if targeted and target_schema is None:
            return ()
        target_seen |= targeted
        component = closed_effect_component_capabilities(
            (effect,), target_schema=(target_schema if legacy_attachment and index == 0 else component_schema)
            if targeted else None, mechanics=mechanics,
        )
        if not component:
            return ()
        dependencies.update(component)
    if target_seen != (target_schema is not None):
        return ()
    return tuple(sorted(dependencies))


__all__ = ["bound_effect_program_node_capabilities"]
