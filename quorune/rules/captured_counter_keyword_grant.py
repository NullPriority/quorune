"""Closed creature-set counter and temporary keyword mode."""

from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects


CAPTURED_COUNTER_KEYWORD_OPERATION = "place_counters_then_grant_captured_keyword"
CAPTURED_COUNTER_KEYWORD_GRANT_FIELD = "captured_counter_keyword_grant"
CAPTURED_COUNTER_KEYWORD_CAPABILITY = "resolution.effect_program.captured_counter_keyword_grant"
CAPTURED_COUNTER_KEYWORD_DEPENDENCIES = (
    CAPTURED_COUNTER_KEYWORD_CAPABILITY,
    "counter.producer.fixed_permanent_set_effect",
    "continuous.resolution.fixed_characteristics_until_end_of_turn",
    "combat.attack.vigilance",
)
CAPTURED_COUNTER_KEYWORD_MECHANICS = ("cr-122-counters", "cr-611-continuous-effects", "vigilance")
CAPTURED_COUNTER_FLYING_VERSION = 2
CAPTURED_COUNTER_FLYING_CAPABILITY = "resolution.effect_program.captured_counter_flying_until_next_turn"
CAPTURED_COUNTER_FLYING_MECHANICS = ("cr-122-counters", "cr-611-continuous-effects", "flying")
CAPTURED_COUNTER_FLYING_DEPENDENCIES = (
    CAPTURED_COUNTER_FLYING_CAPABILITY, CAPTURED_COUNTER_KEYWORD_CAPABILITY,
    "counter.producer.fixed_permanent_set_effect", "continuous.resolution.until_controller_next_turn",
    "combat.block.flying",
)
CAPTURED_LEGENDARY_COUNTER_KEYWORD_VERSION = 3
CAPTURED_LEGENDARY_COUNTER_KEYWORD_CAPABILITY = "resolution.effect_program.captured_legendary_counter_keyword_grant"
CAPTURED_LEGENDARY_CAST_KEYWORDS = {"W": "Vigilance", "B": "Menace"}
_LEGENDARY_KEYWORD_CAPABILITIES = {"Vigilance": "combat.attack.vigilance", "Menace": "combat.block.menace"}
_CONTROLLED_LAND = FrozenMap({"field": "controller", "op": "eq", "value": "$source.controller"})


def captured_counter_keyword_instruction(player="$controller", *, version=1):
    return {
        "op": CAPTURED_COUNTER_KEYWORD_OPERATION,
        "schema_version": version,
        "player": player,
        "query": ObjectQuerySpec(
            zones=("battlefield",), controller=player, types_all=("creature",),
        ).canonical_dict(),
    }


def legendary_counter_keyword_cast_condition(color):
    return {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": "colors", "op": "contains_any", "value": [color]},
    ]}


def captured_legendary_counter_keyword_instruction(color, *, player="$controller"):
    if type(color) is not str or color not in CAPTURED_LEGENDARY_CAST_KEYWORDS:
        raise ValueError("The legendary cohort requires a supported committed cast color")
    return {
        "op": CAPTURED_COUNTER_KEYWORD_OPERATION,
        "schema_version": CAPTURED_LEGENDARY_COUNTER_KEYWORD_VERSION,
        "player": player,
        "cast_color": color,
        "keyword": CAPTURED_LEGENDARY_CAST_KEYWORDS[color],
        "query": ObjectQuerySpec(
            zones=("battlefield",), controller=player, types_all=("creature",), supertypes_all=("legendary",),
        ).canonical_dict(),
    }


def captured_legendary_counter_keyword_mechanics(keyword):
    return ("cr-122-counters", "cr-611-continuous-effects", keyword.casefold())


def decode_captured_counter_keyword_instruction(effect, *, player="$controller"):
    if (
        type(player) is not str or not player or not isinstance(effect, Mapping)
        or type(effect.get("schema_version")) is not int
    ):
        raise ValueError("Captured counter placement requires its exact controlled creature set")
    version = effect["schema_version"]
    if version == CAPTURED_LEGENDARY_COUNTER_KEYWORD_VERSION:
        expected = captured_legendary_counter_keyword_instruction(effect.get("cast_color"), player=player)
    elif version in (1, CAPTURED_COUNTER_FLYING_VERSION):
        expected = captured_counter_keyword_instruction(player, version=version)
    else:
        raise ValueError("Captured counter placement requires its exact controlled creature set")
    if thaw_value(effect) != expected:
        raise ValueError("Captured counter placement requires its exact controlled creature set")
    return ObjectQuerySpec.from_dict(effect["query"])


def captured_counter_keyword_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    if len(effects) != 1 or target_schema is not None or cost_schema is not None:
        return ()
    effect, = effects
    try:
        decode_captured_counter_keyword_instruction(effect)
    except (TypeError, ValueError):
        return ()
    if effect["schema_version"] == CAPTURED_LEGENDARY_COUNTER_KEYWORD_VERSION:
        keyword = effect["keyword"]
        if not set(captured_legendary_counter_keyword_mechanics(keyword)).issubset(mechanic_ids):
            return ()
        return (
            CAPTURED_COUNTER_KEYWORD_CAPABILITY, CAPTURED_LEGENDARY_COUNTER_KEYWORD_CAPABILITY,
            "counter.producer.fixed_permanent_set_effect", "continuous.resolution.fixed_characteristics_until_end_of_turn",
            _LEGENDARY_KEYWORD_CAPABILITIES[keyword],
        )
    if effect["schema_version"] == 1 and set(CAPTURED_COUNTER_KEYWORD_MECHANICS).issubset(mechanic_ids):
        return CAPTURED_COUNTER_KEYWORD_DEPENDENCIES
    return ()


def _legendary_counter_keyword_program_dependencies(program, marked):
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE

    if (
        len(marked) != 1 or marked != tuple(program.effects)
        or marked[0].get("schema_version") != CAPTURED_LEGENDARY_COUNTER_KEYWORD_VERSION
        or program.handlers or program.destination is not None or program.trigger_limit is not None
        or (program.event, program.active_zone) != ("spell.cast", "battlefield")
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
    ):
        raise ValueError("A legendary counter and keyword group requires the original cast trigger")
    required = captured_counter_keyword_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=program.coverage, cost_schema=program.cost_schema,
    )
    if not required or thaw_value(program.event_condition) != legendary_counter_keyword_cast_condition(marked[0]["cast_color"]):
        raise ValueError("The legendary cohort lost its closed cast color or result")
    return (*required, "trigger.event.normalized_spell_cast", "trigger.placement.apnap")


def resolving_legendary_counter_keyword(host, item):
    program = host.semantics.get(item.semantic_key)
    if program is None or CAPTURED_LEGENDARY_COUNTER_KEYWORD_CAPABILITY not in program.capability_dependencies:
        raise ValueError("The legendary cohort grant has no originating cast trigger")
    required = _legendary_counter_keyword_program_dependencies(program, tuple(program.effects))
    if not set(required).issubset(program.capability_dependencies) or not host.semantic_program_is_current_trusted(program):
        raise ValueError("The legendary cohort grant lost its original trusted cast trigger")
    return program.effects[0]["keyword"]


def captured_counter_keyword_modal_dependencies(program):
    schema = program.target_schema
    nodes = tuple(nested_effects((program.effects, program.handlers, schema)))
    if any(CAPTURED_COUNTER_KEYWORD_GRANT_FIELD in node for node in nodes):
        raise ValueError("The captured keyword grant can only be emitted during resolution")
    marked = tuple(node for node in nodes if node.get("op") == CAPTURED_COUNTER_KEYWORD_OPERATION)
    if any(node.get("schema_version") == CAPTURED_COUNTER_FLYING_VERSION for node in marked):
        from .loyalty_spell_results import loyalty_spell_result_runtime_dependencies

        return loyalty_spell_result_runtime_dependencies(program)
    if (CAPTURED_LEGENDARY_COUNTER_KEYWORD_CAPABILITY in program.capability_dependencies
            or any(node.get("schema_version") == CAPTURED_LEGENDARY_COUNTER_KEYWORD_VERSION for node in marked)):
        return _legendary_counter_keyword_program_dependencies(program, marked)
    if not marked and CAPTURED_COUNTER_KEYWORD_CAPABILITY not in program.capability_dependencies:
        return ()
    if (
        len(marked) != 1 or program.effects or program.handlers or program.cost_schema is not None
        or (program.event, program.active_zone) != ("land.enter", "battlefield")
        or FrozenMap(program.event_condition) != _CONTROLLED_LAND
        or not isinstance(schema, Mapping) or not isinstance(schema.get("modes"), Mapping)
        or schema.get("min_modes") != 1 or schema.get("max_modes") != 1
    ):
        raise ValueError("Captured counter and keyword mode requires its original controlled land-entry trigger")
    for branch in schema["modes"].values():
        if not isinstance(branch, Mapping) or CAPTURED_COUNTER_KEYWORD_OPERATION not in {
            effect.get("op") for effect in nested_effects(branch.get("effects"))
        }:
            continue
        required = captured_counter_keyword_node_capabilities(
            effects=branch["effects"], target_schema=branch.get("groups") or None,
            mechanic_ids=branch.get("mechanics", ()),
        )
        if required:
            return (*required, "trigger.event.normalized_zone_change", "trigger.placement.apnap")
    raise ValueError("Captured counter and keyword mode lost its complete untargeted instruction")
