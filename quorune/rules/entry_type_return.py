from __future__ import annotations

"""Bind permanent types to the original entered object."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..continuous_effect_model import ContinuousObjectIdentity
from ..errors import GameRuleError
from ..target_forms import SharedPermanentTypeScope
from ..trigger_processing import pin_trigger_context_entry
from .controlled_return import ControlledReturn, uses_shared_entry_return


ENTRY_TYPE_RETURN_CONTEXT = "entry_type_return_entrant"
_PERMANENT_TYPES = SharedPermanentTypeScope.PERMANENT.types
_OBSERVATION_FIELDS = {"identity", "reference", "last_known_types"}


@dataclass(frozen=True, slots=True)
class EntryTypeReturnObservation:
    identity: ContinuousObjectIdentity
    reference: str
    last_known_types: tuple[str, ...] | None = None

    def __post_init__(self):
        if (not isinstance(self.identity, ContinuousObjectIdentity)
                or type(self.reference) is not str or not self.reference):
            raise ValueError("Shared entry return requires the entrant identity and reference")
        types = self.last_known_types
        if types is not None and (
            not isinstance(types, tuple) or any(type(value) is not str for value in types)
            or tuple(sorted(set(types))) != types or not set(types) <= _PERMANENT_TYPES
        ):
            raise ValueError("Shared entry return requires canonical last-known permanent types")

    def to_dict(self):
        return {
            "identity": self.identity.to_dict(), "reference": self.reference,
            "last_known_types": None if self.last_known_types is None else list(self.last_known_types),
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != _OBSERVATION_FIELDS:
            raise ValueError("Shared entry return lost its entrant observation")
        types = value["last_known_types"]
        if types is not None and not isinstance(types, (list, tuple)):
            raise ValueError("Shared entry return last-known types must be a sequence")
        return cls(ContinuousObjectIdentity.from_dict(value["identity"]), value["reference"],
                   None if types is None else tuple(types))

    def identifies(self, card):
        return (card is not None and card.zone == "battlefield" and not card.phased_out
                and card.ref == self.reference and card.object_id == self.identity.object_id
                and card.logical_object_id == self.identity.logical_object_id)


def battlefield_entry_observation(event, context):
    identity = context.get("card_object_identity")
    count = context.get("card_zone_change_counter")
    types = context.get("types")
    if (event != "permanent.enter" or context.get("to") != "battlefield"
            or type(identity) is not str or type(count) is not int or count < 0
            or not isinstance(types, (list, tuple))):
        raise ValueError("An entrant observation requires a committed permanent entry")
    object_id, delimiter, counter = identity.rpartition("@")
    if not delimiter or not object_id or counter != str(count):
        raise ValueError("Entrant observation has inconsistent incarnation")
    return EntryTypeReturnObservation(ContinuousObjectIdentity(object_id, identity), context.get("card"))


def shared_entry_return_trigger_context(program, event, context):
    if not any(uses_shared_entry_return(effect) for effect in program.effects):
        return {}
    observation = battlefield_entry_observation(event, context)
    if "artifact" in context["types"]:
        raise ValueError("Shared entry return requires a committed nonartifact permanent entry")
    return {ENTRY_TYPE_RETURN_CONTEXT: observation.to_dict()}


def battlefield_permanent_types(host, card):
    data = host._effective_card_data(card)
    types, _, _ = host._type_parts(str(data.get("type_line") or ""))
    return tuple(sorted(_PERMANENT_TYPES.intersection(types)))


def pin_entry_type_return_departures(host, cards, *, error_type=GameRuleError):
    departing = {card.object_id: card for card in cards if card.zone == "battlefield" and not card.phased_out}
    if not departing:
        return

    def capture(context):
        observation = EntryTypeReturnObservation.from_dict(context[ENTRY_TYPE_RETURN_CONTEXT])
        card = departing.get(observation.identity.object_id)
        if not observation.identifies(card):
            return None
        return replace(observation, last_known_types=battlefield_permanent_types(host, card)).to_dict()

    try:
        pin_trigger_context_entry(host, ENTRY_TYPE_RETURN_CONTEXT, capture)
    except (KeyError, TypeError, ValueError) as exc:
        raise error_type(str(exc)) from exc


def bind_shared_entry_return(host, effect, item):
    scope = ControlledReturn.from_effect(effect, actor="$controller")
    context = item.context
    if (scope.schema_version != 2 or item.kind != "triggered_ability"
            or context.get("event") != "permanent.enter" or item.targets):
        raise ValueError("Shared entry return requires its untargeted entry trigger")
    observation = EntryTypeReturnObservation.from_dict(context.get(ENTRY_TYPE_RETURN_CONTEXT))
    if (observation.reference != context.get("card")
            or observation.identity.logical_object_id != context.get("card_object_identity")):
        raise ValueError("Shared entry return cannot change the event entrant")
    card = host.state.cards.get(observation.identity.object_id)
    current = observation.identifies(card)
    types = battlefield_permanent_types(host, card) if current else observation.last_known_types
    if types is None:
        raise ValueError("Shared entry return lacks the entrant's last-known public types")
    return ControlledReturn.shared_entry(actor=item.controller, types=types,
                                         exclude_ref=observation.reference if current else None).effect()
