"""A controlled nontoken entry can require a unique public rules name twice."""

from collections.abc import Mapping

from ..carddb_characteristics import public_object_rules_names
from ..event_counter_reference import event_object_identity
from ..replacement.immutable import FrozenMap
from ..trigger_processing import pin_trigger_context_entry


ENTRANT_NAME_ABSENCE_CAPABILITY = "trigger.condition.entrant_name_absence"
ENTRANT_NAME_ABSENCE_FIELD = "entrant_name_absence"
ENTRANT_NAME_ABSENCE_CONTEXT = "entrant_name_absence_before_departure"
ENTRANT_NAME_ABSENCE_COVERAGE = "fixed-entrant-name-absence"
NONTOKEN_CONTROLLED_CREATURE_ENTRY = FrozenMap({"all": [
    {"field": "controller", "op": "eq", "value": "$source.controller"},
    {"field": "token", "op": "eq", "value": False},
]})
NAME_ABSENCE_LEAF = FrozenMap({"field": ENTRANT_NAME_ABSENCE_FIELD, "op": "eq", "value": True})
_DRAW = FrozenMap({"op": "draw", "player": "$controller", "count": 1, "private": True})


def entrant_name_absence_condition(base):
    if not isinstance(base, Mapping) or FrozenMap(base) != NONTOKEN_CONTROLLED_CREATURE_ENTRY:
        return None
    if base["all"][1]["value"] is not False:
        return None
    return {"all": [*base["all"], dict(NAME_ABSENCE_LEAF)]}


def _uses_condition(value):
    if isinstance(value, Mapping):
        return value.get("field") == ENTRANT_NAME_ABSENCE_FIELD or any(_uses_condition(child) for child in value.values())
    return isinstance(value, (tuple, list)) and any(_uses_condition(child) for child in value)


def entrant_name_absence_runtime_dependencies(program):
    if ENTRANT_NAME_ABSENCE_COVERAGE not in program.coverage and not _uses_condition(program.event_condition):
        return ()
    required_coverage = {ENTRANT_NAME_ABSENCE_COVERAGE, "intervening_condition"}
    expected = FrozenMap(entrant_name_absence_condition(NONTOKEN_CONTROLLED_CREATURE_ENTRY))
    if ((program.event, program.active_zone) != ("creature.enter", "battlefield")
            or not required_coverage <= set(program.coverage) or program.target_schema is not None
            or program.cost_schema is not None or program.handlers or len(program.effects) != 1
            or not isinstance(program.event_condition, Mapping) or FrozenMap(program.event_condition) != expected
            or program.event_condition["all"][1]["value"] is not False
            or program.event_condition["all"][2]["value"] is not True
            or FrozenMap(program.effects[0]) != _DRAW or type(program.effects[0]["count"]) is not int
            or program.effects[0]["private"] is not True):
        raise ValueError("Name absence requires the complete original nontoken creature entry and private draw")
    return (ENTRANT_NAME_ABSENCE_CAPABILITY, "trigger.condition.fixed_public_state",
            "trigger.event.normalized_zone_change", "zone.draw.library_to_hand")


def _entrant_identity(context):
    occurrence = event_object_identity("creature.enter", context)
    logical = occurrence["logical_object_id"]
    object_id, delimiter, counter = logical.rpartition("@")
    change = context.get("card_zone_change_counter")
    if (context.get("to") != "battlefield" or context.get("token") is not False
            or "creature" not in context.get("types", ()) or not delimiter or not object_id
            or type(change) is not int or change < 0 or str(change) != counter):
        raise ValueError("Name absence requires one original nontoken creature entry")
    return object_id, logical, occurrence["reference"]


def _is_current_entrant(card, logical, reference):
    return (card is not None and card.zone == "battlefield" and not card.phased_out
            and card.logical_object_id == logical and card.ref == reference)


def _names(host, card, properties):
    return tuple(sorted(set(public_object_rules_names(card, properties, host.card_record(card)))))


def entrant_name_trigger_context(program, event, context):
    if ENTRANT_NAME_ABSENCE_COVERAGE not in program.coverage:
        return {}
    if event != "creature.enter":
        raise ValueError("Name absence requires the actual creature entry")
    _entrant_identity(context)
    return {ENTRANT_NAME_ABSENCE_CONTEXT: None}


def pin_entrant_names_before_departure(host, cards, *, error_type):
    departing = {card.object_id: card for card in cards if card.zone == "battlefield" and not card.phased_out}
    if not departing:
        return

    def capture(context):
        object_id, logical, reference = _entrant_identity(context)
        card = departing.get(object_id)
        if not _is_current_entrant(card, logical, reference):
            return None
        return {"logical_object_id": logical, "names": list(_names(host, card, host._effective_card_data(card)))}

    try:
        pin_trigger_context_entry(host, ENTRANT_NAME_ABSENCE_CONTEXT, capture)
    except (KeyError, TypeError, ValueError) as exc:
        raise error_type(str(exc)) from exc


def _last_public_names(context, logical):
    snapshot = context.get(ENTRANT_NAME_ABSENCE_CONTEXT)
    if (not isinstance(snapshot, Mapping) or set(snapshot) != {"logical_object_id", "names"}
            or snapshot["logical_object_id"] != logical or not isinstance(snapshot["names"], (list, tuple))
            or any(type(name) is not str or not name for name in snapshot["names"])
            or tuple(snapshot["names"]) != tuple(sorted(set(snapshot["names"])))):
        raise ValueError("Name absence lost the original entrant's last-known public name")
    return snapshot["names"]


def entrant_name_absence_actual(host, condition, context, controller):
    if FrozenMap(condition) != NAME_ABSENCE_LEAF or condition["value"] is not True:
        raise ValueError("Unsupported original entrant name condition")
    return entrant_name_absence_holds(host, context, controller)


def recheck_entrant_name_absence(host, context, controller):
    if context.get("event") != "creature.enter" or ENTRANT_NAME_ABSENCE_CONTEXT not in context:
        raise ValueError("The resolving name condition lost its actual entry")
    return entrant_name_absence_holds(host, context, controller, resolving=True)


def entrant_name_absence_holds(host, context, controller, *, resolving=False):
    if (type(controller) is not str or controller not in host.state.players
            or not resolving and controller != context.get("controller")):
        raise ValueError("Name absence requires the original entry and resolving controller")
    object_id, logical, reference = _entrant_identity(context)
    current = host.state.cards.get(object_id)
    if _is_current_entrant(current, logical, reference):
        names = _names(host, current, host._effective_card_data(current))
    elif resolving:
        names = _last_public_names(context, logical)
    else:
        raise ValueError("Name absence must start from the committed current entrant")
    compared_names = frozenset(names)
    if not compared_names:
        return True

    for card in host.state.cards.values():
        battlefield = (card.zone == "battlefield" and card.controller == controller and not card.phased_out
                       and card.logical_object_id != logical)
        graveyard = (card.zone == "graveyard" and card.owner == controller and card.object_kind == "card")
        if not (battlefield or graveyard):
            continue
        properties = host._effective_card_data(card)
        types, _, _ = host._type_parts(str(properties.get("type_line") or ""))
        if "creature" in types and not compared_names.isdisjoint(_names(host, card, properties)):
            return False
    return True
