"""Closed CR 607 pairing for a graveyard redirect and its current exile play."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..continuous_effect_model import ContinuousObjectIdentity
from ..linked_ability_exile_model import LINKED_ABILITY_EXILE_CAPABILITY
from ..linked_exile_return_model import ExiledObjectIdentity, LINKED_ABILITY_BINDING_PREFIX
from ..zone_object_qualifications import ZONE_OBJECT_QUALIFICATIONS_V1

REPLACEMENT_HANDLER = "replacement.zone.linked-opponent-graveyard-exile.v1"
PERMISSION_HANDLER = "action.permission.linked-graveyard-exile-play.v1"
REPLACEMENT_EVENT = "zone.change"
PERMISSION_EVENT = "action.permission"
REPLACEMENT_CAPABILITY = "zone.change.linked_opponent_graveyard_exile"
PLAY_CAPABILITY = "cast.exile.linked_mana_value_life"
REPLACEMENT_DEPENDENCIES = (
    REPLACEMENT_CAPABILITY, LINKED_ABILITY_EXILE_CAPABILITY, "zone.change.destination_replacement",
)
PLAY_DEPENDENCIES = (PLAY_CAPABILITY, REPLACEMENT_CAPABILITY, LINKED_ABILITY_EXILE_CAPABILITY)

PRIOR_CONTROL_FIELD = "linked_graveyard_prior_controller_v1"
EXILE_LINK_FIELD = "linked_graveyard_exile_source_v1"


@dataclass(frozen=True, slots=True)
class LinkedGraveyardPlaySpec:
    ability_pair: str

    def __post_init__(self):
        if (type(self.ability_pair) is not str
                or not self.ability_pair.startswith(LINKED_ABILITY_BINDING_PREFIX)
                or len(self.ability_pair.removeprefix(LINKED_ABILITY_BINDING_PREFIX)) != 24
                or any(value not in "0123456789abcdef"
                       for value in self.ability_pair.removeprefix(LINKED_ABILITY_BINDING_PREFIX))):
            raise ValueError("The graveyard replacement and exile permission require an exact pair")

    def descriptor(self, *, replacement: bool):
        return {
            "handler_id": REPLACEMENT_HANDLER if replacement else PERMISSION_HANDLER,
            "event": REPLACEMENT_EVENT if replacement else PERMISSION_EVENT,
            "schema_version": 1,
            "ability_pair": self.ability_pair,
        }

    @classmethod
    def from_descriptor(cls, value, *, replacement: bool):
        if not isinstance(value, Mapping) or set(value) != {"handler_id", "event", "schema_version", "ability_pair"}:
            raise ValueError("Graveyard-exile play has a closed versioned descriptor")
        spec = cls(value["ability_pair"])
        if type(value["schema_version"]) is not int or value != spec.descriptor(replacement=replacement):
            raise ValueError("Graveyard-exile play has the wrong handler, event or version")
        return spec

    def conditions(self, controller):
        if type(controller) is not str or not controller:
            raise ValueError("Graveyard exile requires its actual source controller")
        return {
            "destination": {"eq": "graveyard"},
            "owner": {"not_in": [controller]},
            PRIOR_CONTROL_FIELD: {"not_in": [controller]},
            ZONE_OBJECT_QUALIFICATIONS_V1: {"contains": "card"},
        }


def checked_exile_marker(raw, effect_id):
    if (not isinstance(raw, Mapping) or set(raw) != {"effect_id", "source", "ability_pair"}
            or type(effect_id) is not str or not effect_id.startswith(REPLACEMENT_HANDLER + ":")
            or effect_id != raw["effect_id"]):
        raise ValueError("Linked exile requires the original replacement identity")
    return ContinuousObjectIdentity.from_dict(raw["source"]), LinkedGraveyardPlaySpec(raw["ability_pair"])


def committed_linked_graveyard_exile(host, card, prepared):
    """Link only an actual exile arrival carrying this applied replacement's proof."""
    if card.zone != "exile" or card.is_token or card.is_spell_copy or prepared.event is None:
        return
    event = prepared.event
    raw = event.payload.get(EXILE_LINK_FIELD)
    if raw is None:
        return
    effect_id = raw.get("effect_id") if isinstance(raw, Mapping) else None
    source, spec = checked_exile_marker(raw, effect_id)
    if (effect_id not in event.applied_effects or not any(
                effect.effect_id == effect_id and any(
                    operation.to_dict() == {"op": "set", "field": EXILE_LINK_FIELD, "value": raw}
                    for operation in effect.operations
                ) for effect in prepared.effects
            )):
        raise ValueError("Linked exile requires its applied replacement identity")
    from ..linked_ability_exile_state import record_linked_ability_exile
    record_linked_ability_exile(host, source=source, ability_pair=spec.ability_pair, objects=(
        ExiledObjectIdentity(card.object_id, card.ref, card.logical_object_id),
    ))
