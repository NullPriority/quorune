from __future__ import annotations

"""Bind an exchange against the original entrant at each targeting boundary."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..control_exchange_model import PERMANENT_CONTROL_EXCHANGE_OPERATION, PermanentControlExchange
from ..entry_control_exchange_model import ENTRY_EXCHANGE_TARGET_FIELD, ENTRY_EXCHANGE_TYPES, EntryExchangeTarget
from ..errors import GameRuleError
from ..replacement.immutable import thaw_value
from .entry_type_return import EntryTypeReturnObservation, battlefield_entry_observation, battlefield_permanent_types


ENTRY_EXCHANGE_CONTEXT = "entry_exchange_entrant"


@dataclass(frozen=True, slots=True)
class EntryExchangeObservation:
    entrant: EntryTypeReturnObservation
    last_known_controller: str | None = None

    def __post_init__(self):
        if not isinstance(self.entrant, EntryTypeReturnObservation):
            raise ValueError("An entry exchange requires its original entrant")
        if (self.last_known_controller is None) != (self.entrant.last_known_types is None):
            raise ValueError("An entrant's departure pins both its controller and its types")
        EntryExchangeTarget(self.last_known_controller, self.entrant.last_known_types or ())

    def to_dict(self):
        return {"entrant": self.entrant.to_dict(), "last_known_controller": self.last_known_controller}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"entrant", "last_known_controller"}:
            raise ValueError("The original entry exchange observation is unavailable")
        return cls(EntryTypeReturnObservation.from_dict(value["entrant"]), value["last_known_controller"])


def uses_entry_control_exchange(program):
    effects = getattr(program, "effects", ())
    return (len(effects) == 1 and effects[0].get("op") == PERMANENT_CONTROL_EXCHANGE_OPERATION
            and effects[0].get("scope") == PermanentControlExchange.ENTRY_SHARED_TYPE.value)


def entry_exchange_trigger_context(program, event, context):
    if not uses_entry_control_exchange(program):
        return {}
    entry = battlefield_entry_observation(event, context)
    if ENTRY_EXCHANGE_TYPES.isdisjoint(context["types"]):
        raise ValueError("Entry exchange requires an artifact, creature, or enchantment entry")
    return {ENTRY_EXCHANGE_CONTEXT: EntryExchangeObservation(entry).to_dict()}


def _observed_entry_target(host, item):
    context = item.context
    observation = EntryExchangeObservation.from_dict(context.get(ENTRY_EXCHANGE_CONTEXT))
    entrant = observation.entrant
    if (item.kind != "triggered_ability" or context.get("event") != "permanent.enter"
            or context.get("card") != entrant.reference
            or context.get("card_object_identity") != entrant.identity.logical_object_id):
        raise ValueError("Entry exchange cannot replace its original entrant")
    card = host.state.cards.get(entrant.identity.object_id)
    if entrant.identifies(card):
        return EntryExchangeTarget(card.controller, battlefield_permanent_types(host, card))
    return EntryExchangeTarget(observation.last_known_controller, entrant.last_known_types or ())


def bind_entry_exchange_target_schema(host, item, program, schema):
    if not uses_entry_control_exchange(program):
        if isinstance(schema, Mapping) and ENTRY_EXCHANGE_TARGET_FIELD in schema:
            raise GameRuleError("An entry exchange target requires its complete program")
        return schema
    expected = PermanentControlExchange.ENTRY_SHARED_TYPE.target_schema()
    if not isinstance(schema, Mapping) or thaw_value({
        key: value for key, value in schema.items() if key != ENTRY_EXCHANGE_TARGET_FIELD
    }) != expected:
        raise GameRuleError("Entry exchange requires its one permanent target")
    try:
        subject = _observed_entry_target(host, item)
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    return {**expected, ENTRY_EXCHANGE_TARGET_FIELD: subject.to_dict()}


def entry_exchange_target_chooser(schema):
    return EntryExchangeTarget.from_dict(schema[ENTRY_EXCHANGE_TARGET_FIELD]).controller


def pin_entry_exchange_departures(host, cards, *, error_type=GameRuleError):
    from ..trigger_processing import pin_trigger_context_entry

    departing = {card.object_id: card for card in cards if card.zone == "battlefield" and not card.phased_out}
    if not departing:
        return

    def capture(context):
        observation = EntryExchangeObservation.from_dict(context[ENTRY_EXCHANGE_CONTEXT])
        entrant = observation.entrant
        card = departing.get(entrant.identity.object_id)
        if not entrant.identifies(card):
            return None
        entrant = replace(entrant, last_known_types=battlefield_permanent_types(host, card))
        return EntryExchangeObservation(entrant, card.controller).to_dict()

    try:
        pin_trigger_context_entry(host, ENTRY_EXCHANGE_CONTEXT, capture)
    except (KeyError, TypeError, ValueError) as exc:
        raise error_type(str(exc)) from exc
