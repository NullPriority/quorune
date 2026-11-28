"""Fixed stats and untap use the same resolved permanent incarnations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import contains_nested_key


TARGET_PLAYER_STAT_UNTAP_VERSION = "target_player_stat_untap_version"
TARGET_PLAYER_STAT_UNTAP_CAPABILITY = "continuous.resolution.target_player_stats_then_untap"
TARGET_PLAYER_STAT_UNTAP_MECHANICS = ("cr-115-targets", "cr-611-continuous-effects", "tap-and-untap")
CONTROLLER_SUBTYPE_STAT_UNTAP_CAPABILITY = "continuous.resolution.controller_subtype_stats_then_untap"
CONTROLLER_SUBTYPE_STAT_UNTAP_MECHANICS = (
    "cr-611-continuous-effects", "tap-and-untap", "trigger-event-normalized-spell-cast",
)
_CONTROLLER_NONCREATURE_CAST = FrozenMap({"all": [
    {"field": "controller", "op": "eq", "value": "$source.controller"},
    {"not": {"field": "types", "op": "contains_any", "value": ["creature"]}},
]})
_OPERATION = "modify_all_matching_permanents_until_end_of_turn"
_TARGET = "$target.0"
_FIELDS = {"op", TARGET_PLAYER_STAT_UNTAP_VERSION, "player", "power", "toughness"}


def target_player_stat_untap_schema():
    return {"zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1}


@dataclass(frozen=True, slots=True)
class TargetPlayerStatUntapSpec:
    power: int
    toughness: int

    def __post_init__(self):
        if type(self.power) is not int or type(self.toughness) is not int or not (self.power or self.toughness):
            raise ValueError("Player creature stats require fixed nonzero additive deltas")

    def to_effect(self):
        return {
            "op": _OPERATION, TARGET_PLAYER_STAT_UNTAP_VERSION: 1, "player": _TARGET,
            "power": self.power, "toughness": self.toughness,
        }

    @staticmethod
    def receivers(player):
        return ObjectQuerySpec(zones=("battlefield",), controller=player, types_all=("creature",))


@dataclass(frozen=True, slots=True)
class ControllerSubtypeStatUntapSpec:
    power: int
    toughness: int
    subtypes: tuple[str, ...]

    def __post_init__(self):
        TargetPlayerStatUntapSpec(self.power, self.toughness)
        if (type(self.subtypes) is not tuple or not 1 <= len(self.subtypes) <= 4
                or any(type(value) is not str or canonical_creature_subtype(value) != value
                       for value in self.subtypes)
                or self.subtypes != tuple(sorted(set(self.subtypes)))):
            raise ValueError("Controller stats and untap require a fixed canonical subtype set")

    def to_effect(self):
        return {
            "op": _OPERATION, TARGET_PLAYER_STAT_UNTAP_VERSION: 2, "player": "$controller",
            "power": self.power, "toughness": self.toughness, "subtypes": list(self.subtypes),
        }

    def receivers(self, player):
        return ObjectQuerySpec(zones=("battlefield",), controller=player, subtypes_any=self.subtypes)


def decode_target_player_stat_untap(effect, *, resolved=False):
    version = effect.get(TARGET_PLAYER_STAT_UNTAP_VERSION) if isinstance(effect, Mapping) else None
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Stats and untap require a supported versioned instruction")
    fields = _FIELDS if version == 1 else _FIELDS | {"subtypes"}
    allowed = fields | {"reason", "_runtime_source"} if resolved else fields
    if not fields <= set(effect) <= allowed or effect["op"] != _OPERATION:
        raise ValueError("Stats and untap require the exact versioned instruction")
    player = effect["player"]
    role = _TARGET if version == 1 else "$controller"
    if (type(player) is not str or (resolved and (not player or player.startswith("$")))
            or (not resolved and player != role)):
        raise ValueError("Stats and untap require the original bound player role")
    if version == 1:
        return TargetPlayerStatUntapSpec(effect["power"], effect["toughness"]), player
    subtypes = effect["subtypes"]
    if not isinstance(subtypes, (list, tuple)):
        raise ValueError("Controller stats and untap require an ordered subtype array")
    return ControllerSubtypeStatUntapSpec(effect["power"], effect["toughness"], tuple(subtypes)), player


def target_player_stat_untap_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1:
        return ()
    try:
        spec, _ = decode_target_player_stat_untap(effects[0])
    except (TypeError, ValueError):
        return ()
    shared = ("continuous.resolution.fixed_characteristics_until_end_of_turn", "permanent.tap_state.fixed_set")
    if isinstance(spec, ControllerSubtypeStatUntapSpec):
        if target_schema is not None or not set(CONTROLLER_SUBTYPE_STAT_UNTAP_MECHANICS).issubset(mechanic_ids):
            return ()
        return (CONTROLLER_SUBTYPE_STAT_UNTAP_CAPABILITY, *shared, "trigger.event.normalized_spell_cast")
    if (not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or thaw_value(target_schema) != target_player_stat_untap_schema()
            or not set(TARGET_PLAYER_STAT_UNTAP_MECHANICS).issubset(mechanic_ids)):
        return ()
    return (TARGET_PLAYER_STAT_UNTAP_CAPABILITY, *shared, "target.revalidate_resolution")


def target_player_stat_untap_runtime_dependencies(program):
    if not contains_nested_key((program.effects, program.handlers), TARGET_PLAYER_STAT_UNTAP_VERSION):
        return ()
    required = target_player_stat_untap_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=(*TARGET_PLAYER_STAT_UNTAP_MECHANICS, *CONTROLLER_SUBTYPE_STAT_UNTAP_MECHANICS),
    )
    if not required or program.cost_schema is not None or program.handlers or program.trigger_limit is not None:
        raise ValueError("Stats and untap require the complete original trigger")
    if CONTROLLER_SUBTYPE_STAT_UNTAP_CAPABILITY in required:
        if (program.event, program.active_zone) != ("spell.cast", "battlefield") or FrozenMap(
            program.event_condition or {}
        ) != _CONTROLLER_NONCREATURE_CAST:
            raise ValueError("Controller subtype stats and untap require the original noncreature cast")
    elif (program.event, program.active_zone) != ("permanent.enter.self", "battlefield") or program.event_condition is not None:
        raise ValueError("Player creature stats and untap require the complete original self-entry trigger")
    return required
