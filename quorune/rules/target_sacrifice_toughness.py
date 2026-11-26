"""The chosen creature's last public toughness supplies one closed life result."""

from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


TARGET_SACRIFICE_TOUGHNESS_FIELD = "target_sacrifice_toughness_life"
TARGET_SACRIFICE_TOUGHNESS_CAPABILITY = "choice.affected_player.target_sacrifice_toughness_life"
TARGET_SACRIFICE_TOUGHNESS_TEMPLATE = "target-sacrifice-toughness-life-v1"
_TARGET = "$target.0"
_CONTROLLER = "$controller"
_COMBINATIONS = {("any", "target"), ("any", "controller"), ("opponent", "controller")}
_CREATURE = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))


def target_sacrifice_toughness_effect(relation, recipient, *, actor=_CONTROLLER, target=_TARGET):
    from ..compiler.affected_player_sacrifice_templates import (
        AffectedPlayerSacrificeSubject, FixedAffectedPlayerSacrificeTemplate,
    )

    subject = AffectedPlayerSacrificeSubject.TARGET_OPPONENT if relation == "opponent" else AffectedPlayerSacrificeSubject.TARGET_PLAYER
    base = FixedAffectedPlayerSacrificeTemplate(subject, 1, _CREATURE).effects[0]
    return {
        **base, "actor": actor, "target": target, "players": [target],
        TARGET_SACRIFICE_TOUGHNESS_FIELD: {"schema_version": 1, "relation": relation, "recipient": recipient},
    }


def target_sacrifice_toughness_schema(relation):
    return {"zones": ["player"], "categories": ["player"], "player_relation": relation, "count": 1}


def validate_target_sacrifice_toughness(effect, *, actor=_CONTROLLER, target=_TARGET):
    if not isinstance(effect, Mapping):
        raise ValueError("Sacrificed toughness requires the original player choice")
    spec = effect.get(TARGET_SACRIFICE_TOUGHNESS_FIELD)
    if (not isinstance(spec, Mapping) or set(spec) != {"schema_version", "relation", "recipient"}
            or type(spec["schema_version"]) is not int or spec["schema_version"] != 1
            or type(spec["relation"]) is not str or type(spec["recipient"]) is not str
            or (spec["relation"], spec["recipient"]) not in _COMBINATIONS):
        raise ValueError("Sacrificed toughness requires an exact life recipient and target relation")
    relation, recipient = spec["relation"], spec["recipient"]
    if (type(effect.get("count")) is not int
            or FrozenMap(effect) != FrozenMap(target_sacrifice_toughness_effect(
                relation, recipient, actor=actor, target=target,
            ))
            or relation == "opponent" and actor == target):
        raise ValueError("Sacrificed toughness lost the original target's controlled creature choice")
    return relation, recipient


def target_sacrifice_toughness_capabilities(effect, target_schema):
    relation, _recipient = validate_target_sacrifice_toughness(effect)
    if (not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or FrozenMap(target_schema) != FrozenMap(target_sacrifice_toughness_schema(relation))):
        raise ValueError("Sacrificed toughness needs exactly one original player target")
    return (TARGET_SACRIFICE_TOUGHNESS_CAPABILITY, "choice.affected_player.fixed_sacrifice",
            "target.revalidate_resolution", "zone.change.destination_replacement", "life.change.effect")


def target_sacrifice_toughness_runtime_dependencies(program):
    effects = (*program.effects, *program.handlers)
    marked = tuple(effect for effect in nested_effects(effects) if TARGET_SACRIFICE_TOUGHNESS_FIELD in effect)
    if not marked and TARGET_SACRIFICE_TOUGHNESS_CAPABILITY not in program.capability_dependencies:
        return ()
    if ((program.event, program.active_zone) != ("resolve", "stack")
            or len(marked) != 1 or tuple(program.effects) != marked or program.handlers):
        raise ValueError("Sacrificed toughness requires one complete original spell result")
    return target_sacrifice_toughness_capabilities(marked[0], program.target_schema)
