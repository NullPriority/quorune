"""Closed link between color-cast library exile and an opposing cast rule."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..compiler.spell_cast_predicates import (
    FixedSpellCastCharacteristicKind,
    FixedSpellCastCharacteristicQuery,
    FixedSpellCastCharacteristicTerm,
    FixedSpellCastController,
    FixedSpellCastSubject,
)
from ..linked_ability_exile_model import LINKED_ABILITY_EXILE_CAPABILITY
from ..linked_exile_return_model import validate_linked_ability_binding
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects


LINKED_COLOR_TOP_EXILE = "linked_color_cast_exile"
TOP_EXILE_CAPABILITY = "zone.exile.linked_color_cast_top"
EXILE_NAME_CAST_CAPABILITY = "casting.prohibition.linked_exile_rules_names"
EXILE_NAME_CAST_HANDLER = "rule.cast.prohibition.linked-exile-names.v1"
EXILE_NAME_CAST_EVENT = "cast.static.rule"
TOP_EXILE_MECHANICS = ("cr-115-targets", "cr-607-linked-abilities")
TOP_EXILE_REQUIREMENTS = (
    TOP_EXILE_CAPABILITY,
    LINKED_ABILITY_EXILE_CAPABILITY,
    "target.revalidate_resolution",
    "trigger.event.normalized_spell_cast",
    "trigger.placement.apnap",
    "zone.change.destination_replacement",
)
_TARGET = FrozenMap({"zones": ["player"], "categories": ["player"], "count": 1})


@dataclass(frozen=True, slots=True)
class LinkedColorCastExile:
    binding_id: str
    color: str

    def __post_init__(self):
        validate_linked_ability_binding(self.binding_id)
        if self.color not in ("W", "U", "B", "R", "G"):
            raise ValueError("Linked top exile requires exactly one spell color")

    def to_effect(self, *, player="$target.0"):
        return {
            "op": "exile_top_library_card",
            "player": player,
            LINKED_COLOR_TOP_EXILE: {
                "schema_version": 1,
                "binding_id": self.binding_id,
                "color": self.color,
            },
        }

    @classmethod
    def from_effect(cls, effect, *, resolved=False):
        raw = thaw_value(effect)
        if not isinstance(raw, Mapping) or set(raw) != {"op", "player", LINKED_COLOR_TOP_EXILE}:
            raise ValueError("Linked top exile requires one closed target-player instruction")
        payload = raw[LINKED_COLOR_TOP_EXILE]
        if (not isinstance(payload, Mapping) or set(payload) != {"schema_version", "binding_id", "color"}
                or type(payload["schema_version"]) is not int or payload["schema_version"] != 1):
            raise ValueError("Linked top exile requires its exact binding schema")
        player = raw["player"]
        result = cls(payload["binding_id"], payload["color"])
        if ((resolved and (type(player) is not str or not player or player.startswith("$")))
                or raw != result.to_effect(player=player if resolved else "$target.0")):
            raise ValueError("Linked top exile must use only the selected player")
        return result

    @property
    def event_condition(self):
        term = FixedSpellCastCharacteristicTerm(FixedSpellCastCharacteristicKind.COLOR, self.color)
        return FixedSpellCastSubject(
            FixedSpellCastController.SOURCE,
            characteristic_query=FixedSpellCastCharacteristicQuery((term,)),
        ).event_condition


def top_exile_target_schema():
    return thaw_value(_TARGET)


def exile_name_cast_descriptor(binding_id):
    validate_linked_ability_binding(binding_id)
    return {
        "handler_id": EXILE_NAME_CAST_HANDLER,
        "schema_version": 1,
        "event": EXILE_NAME_CAST_EVENT,
        "binding_id": binding_id,
    }


def exile_name_cast_binding(descriptor):
    binding_id = descriptor.get("binding_id") if isinstance(descriptor, Mapping) else None
    if (not isinstance(descriptor, Mapping) or type(descriptor.get("schema_version")) is not int
            or dict(descriptor) != exile_name_cast_descriptor(binding_id)):
        raise ValueError("Linked exile name casting requires its exact descriptor")
    return binding_id


def _linked_cast_members(program):
    producers = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                      if LINKED_COLOR_TOP_EXILE in effect)
    readers = tuple(row for row in program.handlers if row.get("handler_id") == EXILE_NAME_CAST_HANDLER)
    if not producers and not readers:
        if {TOP_EXILE_CAPABILITY, EXILE_NAME_CAST_CAPABILITY}.intersection(program.capability_dependencies):
            raise ValueError("Linked cast authority requires its typed producer or reader")
        return None
    if program.active_zone != "battlefield" or program.cost_schema is not None:
        raise ValueError("Linked cast exiles use the original battlefield ability")
    if producers:
        if (len(producers) != 1 or producers != tuple(program.effects) or program.handlers
                or program.event != "spell.cast" or not isinstance(program.target_schema, Mapping)
                or type(program.target_schema.get("count")) is not int
                or FrozenMap(program.target_schema) != _TARGET
                or not set(TOP_EXILE_MECHANICS) <= set(program.coverage)):
            raise ValueError("A linked color-cast result requires one original targeted trigger")
        spec = LinkedColorCastExile.from_effect(producers[0])
        if FrozenMap(program.event_condition or {}) != FrozenMap(spec.event_condition):
            raise ValueError("Linked color-cast exile lost its original controller and color predicate")
        return spec.binding_id, spec.color
    if (len(readers) != 1 or readers != tuple(program.handlers) or program.effects
            or program.event != EXILE_NAME_CAST_EVENT or program.target_schema is not None
            or program.event_condition is not None):
        raise ValueError("Linked exile names require their one original static cast prohibition")
    return exile_name_cast_binding(readers[0]), None


def linked_color_cast_exile_dependencies(program):
    member = _linked_cast_members(program)
    if member is None:
        return ()
    return TOP_EXILE_REQUIREMENTS if member[1] is not None else (EXILE_NAME_CAST_CAPABILITY,)


def is_closed_linked_color_cast_exile(program):
    try:
        required = linked_color_cast_exile_dependencies(program)
        return TOP_EXILE_CAPABILITY in required and set(required) <= set(program.capability_dependencies)
    except (TypeError, ValueError):
        return False


def validate_linked_color_cast_card_group(programs):
    groups = {}
    for program in programs:
        member = _linked_cast_members(program)
        if member is not None:
            binding, color = member
            groups.setdefault(binding, []).append((program.provenance.get("face_id") or "front", color))
    for members in groups.values():
        faces, colors = zip(*members)
        if len(members) != 3 or len(set(faces)) != 1 or colors.count(None) != 1 or len(set(colors)) != 3:
            raise ValueError("Linked exile names require two distinct color triggers on the same original face")
