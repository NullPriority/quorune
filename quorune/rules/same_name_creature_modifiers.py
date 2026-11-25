from __future__ import annotations

"""Fixed stats for a target creature and its current same-name creatures."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..carddb_characteristics import public_object_rules_names
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import contains_nested_key


SAME_NAME_STAT_VERSION_FIELD = "same_name_set_version"
SAME_NAME_STAT_CAPABILITY = "continuous.resolution.same_name_creature_stat_set"
SAME_NAME_STAT_MECHANICS = ("cr-115-targets", "cr-611-continuous-effects")
_OPERATION = "modify_all_matching_permanents_until_end_of_turn"
_TARGET = "$target.0"
_FIELDS = {"op", SAME_NAME_STAT_VERSION_FIELD, "card", "power", "toughness"}
_CREATURES = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))


@dataclass(frozen=True, slots=True)
class SameNameCreatureStats:
    power: int
    toughness: int

    def __post_init__(self):
        if type(self.power) is not int or type(self.toughness) is not int or not (self.power or self.toughness):
            raise ValueError("Same-name creatures require fixed nonzero additive stats")

    def to_effect(self):
        return {
            "op": _OPERATION,
            SAME_NAME_STAT_VERSION_FIELD: 1,
            "card": _TARGET,
            "power": self.power,
            "toughness": self.toughness,
        }


def same_name_stat_target_schema():
    from ..compiler.direct_target import DirectPermanentTargetSpec

    return DirectPermanentTargetSpec(types_any=("creature",)).to_target_schema()


def decode_same_name_creature_stats(effect, *, resolved=False):
    if not isinstance(effect, Mapping) or set(effect) != _FIELDS or effect["op"] != _OPERATION:
        raise ValueError("Same-name creature stat instruction fields are closed")
    version = effect[SAME_NAME_STAT_VERSION_FIELD]
    if type(version) is not int or version != 1:
        raise ValueError("Unsupported same-name creature stat instruction version")
    ref = effect["card"]
    if type(ref) is not str or (resolved and (not ref or ref.startswith("$"))) or (not resolved and ref != _TARGET):
        raise ValueError("Same-name creature stats require one exact target reference")
    return SameNameCreatureStats(effect["power"], effect["toughness"]), ref


def same_name_creature_stat_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or thaw_value(target_schema) != same_name_stat_target_schema()
            or not set(SAME_NAME_STAT_MECHANICS).issubset(mechanic_ids)):
        return ()
    try:
        decode_same_name_creature_stats(effects[0])
    except ValueError:
        return ()
    return (
        SAME_NAME_STAT_CAPABILITY,
        "continuous.resolution.fixed_characteristics_until_end_of_turn",
        "target.revalidate_resolution",
    )


def same_name_creature_stat_runtime_dependencies(program):
    if not contains_nested_key((program.effects, program.handlers), SAME_NAME_STAT_VERSION_FIELD):
        return ()
    owners = same_name_creature_stat_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=SAME_NAME_STAT_MECHANICS,
    )
    if (not owners or program.event != "resolve" or not program.ability_id.startswith("spell:")
            or contains_nested_key(program.handlers, SAME_NAME_STAT_VERSION_FIELD)):
        raise ValueError("Same-name creature stats require a closed single-target spell result")
    return owners


def resolve_same_name_creature_stats(host, effect):
    """Lock the current public creature set before the ordinary stat batch."""
    from ..continuous_effect_state import matching_battlefield_objects

    spec, ref = decode_same_name_creature_stats(
        {key: value for key, value in effect.items() if key != "_runtime_source"}, resolved=True,
    )
    creatures = matching_battlefield_objects(host, _CREATURES)
    anchor = next((creature for creature in creatures if creature.ref == ref), None)
    if anchor is None:
        raise ValueError("Same-name stats require a current battlefield creature target")
    names = set(public_object_rules_names(anchor, host._effective_card_data(anchor)))
    if not names:
        return spec, (anchor,)
    return spec, tuple(
        creature for creature in creatures
        if creature is anchor or not names.isdisjoint(
            public_object_rules_names(creature, host._effective_card_data(creature))
        )
    )
