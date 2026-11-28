from __future__ import annotations

"""Track and exile only the occurrence created by a controller's discard."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..fixed_top_exile import CHOSEN_IMPULSE_CAPABILITY
from ..discard_effect_origin import DISCARD_BATCH_CAPABILITY, DISCARD_BATCH_EVENT
from ..impulse_access_model import ImpulseAccessDuration
from ..replacement.immutable import thaw_value


DISCARD_EVENT = "card.discarded"
DISCARD_EXILE_CAPABILITY = "zone.exile.own_discard_occurrence"
DISCARD_EXILE_MECHANIC = "discarded-card-graveyard-exile"
DISCARD_EXILE_VERSION = "discarded_card_exile_version"
DISCARD_PLAY_VERSION = "discarded_card_play_version"
DISCARD_PLAY_CAPABILITY = "zone.impulse_access.own_discard_occurrence"
MADNESS_ARRIVAL = "discarded_card_madness_arrival"
DISCARD_EXILE_DEPENDENCIES = (
    DISCARD_EXILE_CAPABILITY,
    "casting.madness.fixed_mana",
    "trigger.effect.fixed_event",
    "trigger.event.normalized_zone_change",
    "trigger.placement.apnap",
    "zone.change.destination_replacement",
    "zone.discard.typed_cause",
)


DISCARD_PLAY_DEPENDENCIES = (
    *DISCARD_EXILE_DEPENDENCIES, DISCARD_PLAY_CAPABILITY, CHOSEN_IMPULSE_CAPABILITY, "effect.choice.optional_fixed",
)


def controller_discard_condition():
    return {"field": "previous_controller", "op": "eq", "value": "$source.controller"}


def discarded_card_exile_effect():
    return {
        "op": "move_if_in_zone",
        DISCARD_EXILE_VERSION: 1,
        "card": "$context.card",
        "discard_destination": "$context.to",
        "discard_zone_change_counter": "$context.card_zone_change_counter",
        "discard_owner": "$context.owner",
        MADNESS_ARRIVAL: "$context." + MADNESS_ARRIVAL,
        "from": "graveyard",
        "destination": "exile",
    }


def discarded_card_play_effect():
    move = discarded_card_exile_effect()
    del move[DISCARD_EXILE_VERSION]
    move[DISCARD_PLAY_VERSION] = 1
    return {"op": "offer_optional_effect", "player": "$controller", "effects": [move]}


def uses_discarded_card_play(effect):
    if isinstance(effect, Mapping):
        return DISCARD_PLAY_VERSION in effect or any(uses_discarded_card_play(value) for value in effect.values())
    return isinstance(effect, (list, tuple)) and any(uses_discarded_card_play(value) for value in effect)


def uses_discarded_card_exile(effect):
    return isinstance(effect, Mapping) and (
        DISCARD_EXILE_VERSION in effect or MADNESS_ARRIVAL in effect or uses_discarded_card_play(effect)
    )


def discarded_card_exile_node_capabilities(*, effects, target_schema, mechanics, cost_schema):
    if any(uses_discarded_card_play(effect) for effect in effects):
        if (DISCARD_EXILE_MECHANIC not in mechanics or target_schema is not None or cost_schema is not None
                or len(effects) != 1 or thaw_value(effects[0]) != discarded_card_play_effect()
                or type(effects[0]["effects"][0][DISCARD_PLAY_VERSION]) is not int):
            return ()
        return DISCARD_PLAY_DEPENDENCIES
    if (
        DISCARD_EXILE_MECHANIC not in mechanics or target_schema is not None or cost_schema is not None
        or len(effects) != 1 or not isinstance(effects[0], Mapping)
        or type(effects[0].get(DISCARD_EXILE_VERSION)) is not int
        or thaw_value(effects[0]) != discarded_card_exile_effect()
    ):
        return ()
    return DISCARD_EXILE_DEPENDENCIES


def discarded_card_exile_runtime_dependencies(program):
    from ..card_programs.admission import program_has_declared_complete_source_admission

    if program.event == DISCARD_BATCH_EVENT:
        expected = {'op': 'fixed_impulse_access', 'player': '$controller', 'count': 1,
                    'duration': ImpulseAccessDuration.NEXT_END_STEP.value}
        if (program.active_zone != 'battlefield' or program.handlers or program.cost_schema is not None
                or program.target_schema is not None or thaw_value(program.event_condition) != controller_discard_condition()
                or len(program.effects) != 1 or thaw_value(program.effects[0]) != expected
                or type(program.effects[0]['count']) is not int):
            raise ValueError('Grouped discards require the controller and fixed next-end-step impulse result')
        return (DISCARD_BATCH_CAPABILITY,)
    if not any(uses_discarded_card_exile(effect) for effect in program.effects):
        return ()
    if (
        program.event != DISCARD_EVENT or program.active_zone != "battlefield"
        or thaw_value(program.event_condition) != controller_discard_condition()
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage or program.handlers
        or not program_has_declared_complete_source_admission(program)
    ):
        raise ValueError("Discarded-card exile requires the controller's committed discard occurrence")
    dependencies = discarded_card_exile_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanics={DISCARD_EXILE_MECHANIC}, cost_schema=program.cost_schema,
    )
    if not dependencies:
        raise ValueError("Discarded-card exile requires its closed nontargeted result")
    return dependencies


def discarded_card_exile_trigger_context(program, event):
    if event == DISCARD_EVENT and any(uses_discarded_card_exile(effect) for effect in program.effects):
        return {MADNESS_ARRIVAL: None}
    return {}


def pin_discarded_madness_arrival(host, card, exile_counter):
    """Retain only the successor created when the native Madness choice declines."""
    if card.zone != "graveyard" or card.zone_change_counter != exile_counter + 1:
        return

    def capture(context):
        if (
            context.get("event") == DISCARD_EVENT and context.get("card") == card.ref
            and context.get("owner") == card.owner and context.get("to") == "exile"
            and context.get("card_zone_change_counter") == exile_counter
        ):
            return card.zone_change_counter
        return None

    from ..trigger_processing import pin_trigger_context_entry

    pin_trigger_context_entry(host, MADNESS_ARRIVAL, capture)


def discarded_card_play_movement(effect, *, actor):
    """Validate a resolved optional result using the committed discard owner."""
    if (type(effect.get(DISCARD_PLAY_VERSION)) is not int or effect[DISCARD_PLAY_VERSION] != 1
            or DISCARD_EXILE_VERSION in effect):
        raise ValueError("Discarded-card play requires its exact scoped version")
    legacy = {key: value for key, value in effect.items() if key != DISCARD_PLAY_VERSION}
    return discarded_card_exile_movement({**legacy, DISCARD_EXILE_VERSION: 1}, actor=actor)


def discarded_card_play_choice(effect, *, actor):
    """Validate one optional choice and the single discard-linked result it owns."""
    values = effect.get("effects")
    if (set(effect) != {"op", "player", "effects"} or effect["op"] != "offer_optional_effect"
            or effect["player"] != actor or not isinstance(values, (list, tuple))
            or len(values) != 1 or not isinstance(values[0], Mapping)):
        raise ValueError("Discarded-card play requires the resolving controller's exact optional effect")
    discarded_card_play_movement(values[0], actor=actor)
    return values[0]


def discarded_card_play_available(effect, *, actor, query):
    movement = discarded_card_play_movement(effect, actor=actor)
    if movement is None:
        return False
    card = query.object(movement["card"], zones=("graveyard",))
    return (card is not None and card.owner == actor
            and card.logical_object_id == f"{card.object_id}@{movement['expected_zone_change_counter']}")


def discarded_card_exile_movement(effect, *, actor):
    """Select the exact public graveyard incarnation for the canonical zone mover."""
    expected = discarded_card_exile_effect()
    identities = {"card", "discard_destination", "discard_zone_change_counter", "discard_owner", MADNESS_ARRIVAL}
    extras = {"_replacement_selections", "_runtime_source"}
    if (
        not identities <= effect.keys() or type(effect.get(DISCARD_EXILE_VERSION)) is not int
        or {key: value for key, value in effect.items() if key not in identities | extras}
        != {key: value for key, value in expected.items() if key not in identities}
    ):
        raise ValueError("Resolved discarded-card exile has unexpected fields")
    counter = effect["discard_zone_change_counter"]
    destination = effect["discard_destination"]
    madness = effect[MADNESS_ARRIVAL]
    if (
        type(counter) is not int or counter < 1
        or any(type(effect[key]) is not str or not effect[key] or effect[key].startswith("$")
               for key in ("card", "discard_owner", "discard_destination"))
        or madness is not None and (type(madness) is not int or madness != counter + 1 or destination != "exile")
    ):
        raise ValueError("Discarded-card exile requires its exact committed arrival")
    if effect["discard_owner"] != actor:
        return None
    graveyard_counter = counter if destination == "graveyard" else madness
    if graveyard_counter is None:
        return None
    return {
        "op": "move_if_in_zone",
        "card": effect["card"],
        "from": "graveyard",
        "destination": "exile",
        "expected_zone_change_counter": graveyard_counter,
        "_replacement_selections": effect.get("_replacement_selections", ()),
    }
