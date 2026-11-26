"""One targeted player's creature snapshot receives fixed stats and untaps."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import contains_nested_key


TARGET_PLAYER_STAT_UNTAP_VERSION = "target_player_stat_untap_version"
TARGET_PLAYER_STAT_UNTAP_CAPABILITY = "continuous.resolution.target_player_stats_then_untap"
TARGET_PLAYER_STAT_UNTAP_MECHANICS = ("cr-115-targets", "cr-611-continuous-effects", "tap-and-untap")
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


def decode_target_player_stat_untap(effect, *, resolved=False):
    allowed = _FIELDS | {"reason", "_runtime_source"} if resolved else _FIELDS
    if (not isinstance(effect, Mapping) or not _FIELDS <= set(effect) <= allowed
            or effect["op"] != _OPERATION or type(effect[TARGET_PLAYER_STAT_UNTAP_VERSION]) is not int
            or effect[TARGET_PLAYER_STAT_UNTAP_VERSION] != 1):
        raise ValueError("Player creature stats and untap require the exact versioned instruction")
    player = effect["player"]
    if (type(player) is not str or (resolved and (not player or player.startswith("$")))
            or (not resolved and player != _TARGET)):
        raise ValueError("Player creature stats and untap require one exact target player")
    return TargetPlayerStatUntapSpec(effect["power"], effect["toughness"]), player


def target_player_stat_untap_node_capabilities(*, effects, target_schema, mechanic_ids):
    # Strictly reject booleans as target counts before comparing the schema.
    if (len(effects) != 1 or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or thaw_value(target_schema) != target_player_stat_untap_schema()
            or not set(TARGET_PLAYER_STAT_UNTAP_MECHANICS).issubset(mechanic_ids)):
        return ()
    try:
        decode_target_player_stat_untap(effects[0])
    except (TypeError, ValueError):
        return ()
    return (
        TARGET_PLAYER_STAT_UNTAP_CAPABILITY, "continuous.resolution.fixed_characteristics_until_end_of_turn",
        "permanent.tap_state.fixed_set", "target.revalidate_resolution",
    )


def target_player_stat_untap_runtime_dependencies(program):
    if not contains_nested_key((program.effects, program.handlers), TARGET_PLAYER_STAT_UNTAP_VERSION):
        return ()
    required = target_player_stat_untap_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=TARGET_PLAYER_STAT_UNTAP_MECHANICS,
    )
    if (not required or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or program.event_condition is not None or program.cost_schema is not None or program.handlers
            or program.trigger_limit is not None):
        raise ValueError("Player creature stats and untap require the complete original self-entry trigger")
    return required
