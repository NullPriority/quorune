"""Closed draw and keyword-grant instructions that share the sampled subjects."""

from collections.abc import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..replacement.immutable import thaw_value
from ..resolution_characteristic_model import CHARACTERISTIC_OPERATION
from .effect_shape_walkers import nested_effects


CAPTURED_DRAW_KEYWORD_OPERATION = "draw_then_grant_captured_keyword"
CAPTURED_DRAW_KEYWORD_GRANT_FIELD = "captured_draw_keyword_grant"
CAPTURED_DRAW_KEYWORD_CAPABILITY = "resolution.effect_program.captured_draw_keyword_grant"
CAPTURED_DRAW_KEYWORD_MECHANICS = ("cr-121-drawing-a-card", "cr-611-continuous-effects", "indestructible")
CAPTURED_DRAW_KEYWORD_DEPENDENCIES = (
    CAPTURED_DRAW_KEYWORD_CAPABILITY,
    "zone.draw.library_to_hand",
    "state_query.permanent.public_state_predicate",
    "continuous.resolution.fixed_characteristics_until_end_of_turn",
    "permanent.indestructible.ordinary",
)


def captured_countered_creature_query(player):
    return ObjectQuerySpec(
        zones=("battlefield",), controller=player, types_all=("creature",),
        state_predicate=PermanentStatePredicateSpec(counter_name="+1/+1", minimum_counter_count=1),
    )


def captured_draw_keyword_instruction(player="$controller"):
    return {
        "op": CAPTURED_DRAW_KEYWORD_OPERATION, "schema_version": 1, "player": player,
        "query": captured_countered_creature_query(player).canonical_dict(),
    }


def decode_captured_draw_keyword_instruction(effect, *, player="$controller"):
    if (type(player) is not str or not player or not isinstance(effect, Mapping)
            or type(effect.get("schema_version")) is not int
            or thaw_value(effect) != captured_draw_keyword_instruction(player)):
        raise ValueError("Captured draw requires its exact controlled countered-creature query")
    return ObjectQuerySpec.from_dict(effect["query"])


def retained_keyword_grant_effect(*, player, identities):
    return {
        "op": CHARACTERISTIC_OPERATION,
        CAPTURED_DRAW_KEYWORD_GRANT_FIELD: {
            "schema_version": 1, "player": player,
            "identities": [identity.to_dict() for identity in identities],
        },
    }


def decode_retained_keyword_grant(effect, *, player):
    fields = {"op", CAPTURED_DRAW_KEYWORD_GRANT_FIELD, "_runtime_source"}
    if (not isinstance(effect, Mapping) or set(effect) - {"reason"} != fields
            or effect["op"] != CHARACTERISTIC_OPERATION):
        raise ValueError("Captured keyword grant requires its exact resolved instruction")
    payload = effect[CAPTURED_DRAW_KEYWORD_GRANT_FIELD]
    if (not isinstance(payload, Mapping) or set(payload) != {"schema_version", "player", "identities"}
            or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
            or payload["player"] != player or not isinstance(payload["identities"], (list, tuple))
            or not payload["identities"]):
        raise ValueError("Captured keyword grant requires the original controller and subjects")
    subjects = tuple(ContinuousObjectIdentity.from_dict(value) for value in payload["identities"])
    if (subjects != tuple(sorted(subjects)) or len({subject.object_id for subject in subjects}) != len(subjects)
            or len({subject.logical_object_id for subject in subjects}) != len(subjects)):
        raise ValueError("Captured keyword grant has duplicate or unordered subjects")
    return subjects


def captured_draw_keyword_grant_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    if (len(effects) != 1 or target_schema is not None
            or not set(CAPTURED_DRAW_KEYWORD_MECHANICS).issubset(mechanic_ids)):
        return ()
    try:
        decode_captured_draw_keyword_instruction(effects[0])
    except (TypeError, ValueError):
        return ()
    return CAPTURED_DRAW_KEYWORD_DEPENDENCIES


def captured_draw_keyword_grant_program_dependencies(program):
    effects = tuple(program.effects)
    nodes = tuple(nested_effects((*effects, *program.handlers)))
    if any(CAPTURED_DRAW_KEYWORD_GRANT_FIELD in node for node in nodes):
        raise ValueError("A captured keyword grant is emitted only by the resolving draw instruction")
    marked = tuple(node for node in nodes if node.get("op") == CAPTURED_DRAW_KEYWORD_OPERATION)
    if not marked and CAPTURED_DRAW_KEYWORD_CAPABILITY not in program.capability_dependencies:
        return ()
    if (effects != marked or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:") or program.handlers or program.event_condition is not None):
        raise ValueError("Captured draw and keyword grant require one complete spell body")
    required = captured_draw_keyword_grant_node_capabilities(
        effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Captured draw and keyword grant lost their closed query or result")
    return required


def is_closed_captured_draw_keyword_grant_program(program):
    try:
        required = captured_draw_keyword_grant_program_dependencies(program)
    except (ValueError, TypeError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
