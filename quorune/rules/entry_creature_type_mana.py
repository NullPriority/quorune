from __future__ import annotations

"""Public current or last-known creature types for the original entrant."""

from collections.abc import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..event_counter_reference import event_object_identity
from ..query_effect_amount_model import (
    ENTRY_SHARED_TYPE_AMOUNT_VERSION, PUBLIC_QUERY_AMOUNT_KIND, PublicQueryAmountSpec,
    entry_shared_type_amount_spec, public_query_amount_runtime_dependencies,
)
from ..replacement.immutable import FrozenMap
from ..shared_creature_type_model import SharedCreatureTypeObject, canonical_creature_types
from ..trigger_processing import pin_trigger_context_entry


ENTRY_SHARED_TYPE_MANA_WRAPPER_VERSION = 7
ENTRY_CREATURE_TYPES_CONTEXT = "entrant_creature_types_before_departure"
_ALL_CREATURES = FrozenMap({"field": "token", "op": "in", "value": [False, True]})


def all_creature_entry_condition(condition):
    if not isinstance(condition, Mapping) or FrozenMap(condition) != _ALL_CREATURES:
        return False
    return all(type(value) is bool for value in condition["value"])


def optional_entry_shared_type_mana_effect():
    return {
        "op": "offer_optional_effect", "player": "$controller",
        "schema_version": ENTRY_SHARED_TYPE_MANA_WRAPPER_VERSION, "effects": [{
            "op": "mana", "source": "$source", "player": "$controller", "color": "C",
            "amount": entry_shared_type_amount_spec().to_dict(),
        }],
    }


def is_optional_entry_shared_type_mana(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "offer_optional_effect"
            and effect.get("schema_version") == ENTRY_SHARED_TYPE_MANA_WRAPPER_VERSION)


def validate_bound_entry_shared_type_mana(effect, actor):
    values = effect.get("effects")
    if (not is_optional_entry_shared_type_mana(effect)
            or type(effect["schema_version"]) is not int
            or set(effect) != {"op", "player", "schema_version", "effects"} or effect["player"] != actor
            or not isinstance(values, (list, tuple)) or len(values) != 1 or not isinstance(values[0], Mapping)):
        raise ValueError("Shared creature-type mana requires its sealed controller and single choice")
    child = values[0]
    if (set(child) != {"op", "source", "player", "color", "amount"} or child["op"] != "mana"
            or child["player"] != actor or child["color"] != "C"
            or type(child["amount"]) is not int or child["amount"] < 0
            or child["source"] is not None and (type(child["source"]) is not str or not child["source"])):
        raise ValueError("Shared creature-type mana requires one fixed colorless award for its controller")
    return child


def optional_entry_shared_type_mana_node_capabilities(effects, target_schema):
    if (target_schema is not None or len(effects) != 1 or not is_optional_entry_shared_type_mana(effects[0])
            or type(effects[0]["schema_version"]) is not int
            or FrozenMap(effects[0]) != FrozenMap(optional_entry_shared_type_mana_effect())):
        return ()
    amount = effects[0]["effects"][0]["amount"]
    try:
        PublicQueryAmountSpec.from_dict(amount)
    except (TypeError, ValueError):
        return ()
    return (
        "effect.choice.optional_fixed", "mana.production.public_quantity",
        *public_query_amount_runtime_dependencies(amount),
    )


def _uses_entry_shared_type_amount(value):
    if isinstance(value, Mapping):
        return (is_optional_entry_shared_type_mana(value)
                or (value.get("kind"), value.get("schema_version")) ==
                (PUBLIC_QUERY_AMOUNT_KIND, ENTRY_SHARED_TYPE_AMOUNT_VERSION)
                or any(_uses_entry_shared_type_amount(child) for child in value.values()))
    return isinstance(value, (list, tuple)) and any(_uses_entry_shared_type_amount(child) for child in value)


def entry_shared_type_mana_runtime_dependencies(program):
    if not _uses_entry_shared_type_amount((program.effects, program.handlers, program.cost_schema, program.target_schema)):
        return ()
    dependencies = optional_entry_shared_type_mana_node_capabilities(program.effects, program.target_schema)
    if (not dependencies or (program.event, program.active_zone) != ("creature.enter", "battlefield")
            or program.handlers or program.cost_schema is not None
            or not all_creature_entry_condition(program.event_condition)):
        raise ValueError("Entrant shared creature-type mana requires its complete optional all-creature entry trigger")
    return (*dependencies, "trigger.effect.fixed_event", "trigger.event.normalized_zone_change", "trigger.placement.apnap")


def _original_entrant(event, facts):
    sealed = event_object_identity(event, facts)
    logical = sealed["logical_object_id"]
    object_id, delimiter, counter = logical.rpartition("@")
    change = facts.get("card_zone_change_counter")
    if (event != "creature.enter" or facts.get("to") != "battlefield" or not delimiter or not object_id
            or type(change) is not int or change < 0 or counter != str(change)
            or "creature" not in facts.get("types", ())):
        raise ValueError("Shared creature types require a committed creature entry")
    return ContinuousObjectIdentity(object_id, logical), sealed["reference"]


def entry_creature_type_trigger_context(program, event, facts):
    if not _uses_entry_shared_type_amount(program.effects):
        return {}
    _original_entrant(event, facts)
    return {ENTRY_CREATURE_TYPES_CONTEXT: None}


def _current_entrant(card, identity, reference):
    return (card is not None and card.zone == "battlefield" and not card.phased_out and card.ref == reference
            and card.logical_object_id == identity.logical_object_id)


def _snapshot(host, card, identity):
    data = host._effective_card_data(card)
    _, subtypes, _ = host._type_parts(str(data.get("type_line") or ""))
    return SharedCreatureTypeObject(identity, canonical_creature_types(subtypes))


def pin_entry_creature_type_departures(host, cards, *, error_type):
    departing = {card.object_id: card for card in cards if card.zone == "battlefield" and not card.phased_out}
    if not departing:
        return

    def capture(context):
        identity, reference = _original_entrant(context.get("event"), context)
        card = departing.get(identity.object_id)
        if _current_entrant(card, identity, reference):
            return _snapshot(host, card, identity).to_dict()
        return None

    try:
        pin_trigger_context_entry(host, ENTRY_CREATURE_TYPES_CONTEXT, capture)
    except (KeyError, TypeError, ValueError) as exc:
        raise error_type(str(exc)) from exc


def current_or_last_entrant_creature_types(host, item):
    if item.kind != "triggered_ability" or item.targets or ENTRY_CREATURE_TYPES_CONTEXT not in item.context:
        raise ValueError("Shared creature types require the original untargeted entry trigger")
    identity, reference = _original_entrant(item.context.get("event"), item.context)
    card = host.state.cards.get(identity.object_id)
    if _current_entrant(card, identity, reference):
        return _snapshot(host, card, identity)
    snapshot = SharedCreatureTypeObject.from_dict(item.context[ENTRY_CREATURE_TYPES_CONTEXT])
    if snapshot.identity != identity:
        raise ValueError("Last-known creature types must belong to the original entrant")
    return snapshot
