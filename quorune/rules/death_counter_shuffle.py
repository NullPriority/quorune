"""Closed source-death counter placement followed by an owner-library shuffle."""

from __future__ import annotations

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..continuous_effect_model import ContinuousObjectIdentity
from ..counter_snapshot import permanent_counter_snapshot
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap


DEATH_COUNTER_SHUFFLE_FIELD = "source_death_counter_shuffle"
DEATH_COUNTER_SHUFFLE_DEPENDENCIES = (
    "counter.producer.fixed_multikind_effect",
    "library.shuffle.fixed_controller",
    "target.revalidate_resolution",
    "trigger.event.normalized_zone_change",
    "trigger.placement.apnap",
    "zone.change.destination_replacement",
)


def death_counter_shuffle_effects():
    return (
        {
            "op": "place_counter_batch",
            "card": "$target.0",
            "placements": "$context.death_counter_placements",
            "source": "$stack",
            DEATH_COUNTER_SHUFFLE_FIELD: 1,
        },
        {
            "op": "shuffle_into_library",
            "card": "$context.death_counter_arrival",
            "owner": "$context.death_counter_owner",
            DEATH_COUNTER_SHUFFLE_FIELD: 1,
        },
    )


def death_counter_shuffle_target_schema():
    return {
        "categories": ["permanent"],
        "zones": ["battlefield"],
        "types_any": ["creature"],
        "controller_relation": "you",
        "count": 1,
    }


def death_counter_shuffle_dependencies(program):
    if not any(DEATH_COUNTER_SHUFFLE_FIELD in effect for effect in program.effects):
        return ()
    if (
        (program.event, program.active_zone) != ("creature.dies.self", "battlefield")
        or program.event_condition is not None
        or program.cost_schema is not None
        or program.handlers
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        or program.target_schema is None
        or FrozenMap(program.target_schema) != FrozenMap(death_counter_shuffle_target_schema())
        or tuple(map(FrozenMap, program.effects)) != tuple(map(FrozenMap, death_counter_shuffle_effects()))
        or any(type(effect[DEATH_COUNTER_SHUFFLE_FIELD]) is not int for effect in program.effects)
    ):
        raise ValueError("Departure counter placement requires its own death, controlled target and owner shuffle")
    return DEATH_COUNTER_SHUFFLE_DEPENDENCIES


def resolve_departure_shuffle(state, active_seats, effect):
    """Validate the captured owner and locate the exact current graveyard card."""
    marker = effect[DEATH_COUNTER_SHUFFLE_FIELD]
    owner = effect.get("owner")
    allowed = {
        "op", "card", "owner", DEATH_COUNTER_SHUFFLE_FIELD, "reason",
        "_replacement_selections", "_runtime_source",
    }
    if (
        set(effect) - allowed
        or type(marker) is not int
        or marker != 1
        or type(owner) is not str
        or owner not in state.players
    ):
        raise GameRuleError("A departure shuffle requires the captured owner")
    if owner not in active_seats:
        return None
    try:
        arrival = ContinuousObjectIdentity.from_dict(effect.get("card"))
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError("A departure shuffle requires the captured graveyard object") from exc
    card = state.cards.get(arrival.object_id)
    if (
        card is None
        or card.zone != "graveyard"
        or card.logical_object_id != arrival.logical_object_id
        or not card.is_card_object
    ):
        return owner, None
    if card.owner != owner:
        raise GameRuleError("The departure shuffle's current card owner changed")
    return owner, card


def death_counter_shuffle_context(source, program, event, occurrence):
    if not death_counter_shuffle_dependencies(program):
        return {}
    change = occurrence.get("card_zone_change_counter")
    if (event != "creature.dies" or occurrence.get("card") != source.ref
            or occurrence.get("from") != "battlefield" or occurrence.get("to") != "graveyard"
            or occurrence.get("card_object_identity") != source.logical_object_id
            or type(change) is not int or change != source.zone_change_counter + 1):
        raise ValueError("Departure counters require the original battlefield departure")
    snapshot = permanent_counter_snapshot(source.counters)
    return {
        "death_counter_arrival": ContinuousObjectIdentity(source.object_id, f"{source.object_id}@{change}").to_dict(),
        "death_counter_placements": [
            {"counter": name, "amount": count} for name, count in sorted(snapshot.items())
        ],
        "death_counter_owner": source.owner,
    }
