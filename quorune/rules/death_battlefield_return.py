"""Return the exact physical cards of a qualified creature death together."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..characteristic_evaluation import type_parts
from ..continuous_effect_state import ResolutionEffectSource
from ..delayed_object_model import DEATH_BATTLEFIELD_ARRIVALS, SOURCE_DEATH_HAND_CAPABILITY, SOURCE_DEATH_HAND_EVENT
from ..entry_counter_model import EffectEntryCounter
from ..errors import GameRuleError
from ..external_entry_state_model import PERMANENT_CARD_TYPES
from ..replacement.immutable import FrozenMap


FLYING_EVENT_BATTLEFIELD_RETURN_VERSION = 9
DEATH_BATTLEFIELD_RETURN_CAPABILITY = "zone.return.fixed_controlled_death_battlefield"
DEATH_BATTLEFIELD_RETURN_MECHANIC = "fixed-controlled-death-battlefield-return"
DEATH_BATTLEFIELD_RETURN_DEPENDENCIES = (
    DEATH_BATTLEFIELD_RETURN_CAPABILITY, "trigger.event.qualified_zone_change",
    "counter.characteristic.keyword", "counter.producer.effect_entry", "zone.change.destination_replacement",
)


@dataclass(frozen=True, slots=True)
class DeathBattlefieldArrival:
    object_id: str
    reference: str
    zone_change_counter: int
    owner: str

    def __post_init__(self) -> None:
        if (any(type(value) is not str or not value or value != value.strip()
                for value in (self.object_id, self.reference, self.owner))
                or type(self.zone_change_counter) is not int or self.zone_change_counter < 1):
            raise ValueError("Death return requires an exact public card incarnation and its owner")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "object_id": self.object_id, "reference": self.reference,
            "zone_change_counter": self.zone_change_counter, "owner": self.owner,
        }

    @classmethod
    def from_dict(cls, value: object) -> DeathBattlefieldArrival:
        if not isinstance(value, Mapping) or set(value) != {
            "object_id", "reference", "zone_change_counter", "owner",
        }:
            raise ValueError("Death return has an invalid physical arrival")
        return cls(**value)

    def matches(self, card) -> bool:
        return (card is not None and card.ref == self.reference
                and card.zone == "graveyard" and card.zone_change_counter == self.zone_change_counter
                and card.owner == self.owner and card.is_card_object and not card.is_token)


def controlled_nonflying_death_condition() -> dict:
    return {"all": [
        {"field": "types", "op": "contains_any", "value": ["creature"]},
        {"not": {"field": "keywords", "op": "contains_any", "value": ["flying"]}},
        {"field": "controller", "op": "eq", "value": "$source.controller"},
    ]}


def death_battlefield_return_effect() -> dict:
    return {
        "op": "reanimate", "schema_version": FLYING_EVENT_BATTLEFIELD_RETURN_VERSION,
        DEATH_BATTLEFIELD_ARRIVALS: "$context." + DEATH_BATTLEFIELD_ARRIVALS,
        "source": "$stack",
    }


def is_death_battlefield_return(effect) -> bool:
    return effect.get("op") == "reanimate" and (
        effect.get("schema_version") == FLYING_EVENT_BATTLEFIELD_RETURN_VERSION
        or DEATH_BATTLEFIELD_ARRIVALS in effect and "phase" not in effect
    )


def death_battlefield_return_context(host, context, occurrence, parts, *, permanent_graveyard=False):
    """Keep the public arrivals before any trigger is offered or token removed."""
    from .event_subscriptions import event_has_capability_subscriber
    from .linked_death_upkeep import CAPABILITY as LINKED_DEATH_UPKEEP_CAPABILITY
    from .opposing_death_delayed_return import OPPOSING_DEATH_DELAYED_RETURN_CAPABILITY
    from .granted_death_role_return import GRANTED_DEATH_ROLE_RETURN_CAPABILITY, has_granted_self_death_return
    from .revival_return import CAPABILITY as REVIVAL_CAPABILITY, capture_revival_death_count

    revival = not permanent_graveyard and event_has_capability_subscriber(
        host, "creature.dies.self", REVIVAL_CAPABILITY,
    )
    if permanent_graveyard and not (
        has_granted_self_death_return(host, occurrence.previous_characteristics)
        or event_has_capability_subscriber(host, SOURCE_DEATH_HAND_EVENT, SOURCE_DEATH_HAND_CAPABILITY)
    ):
        return context
    watched = (
        ("creature.dies", DEATH_BATTLEFIELD_RETURN_CAPABILITY),
        ("creature.dies", OPPOSING_DEATH_DELAYED_RETURN_CAPABILITY),
        ("creature.dies", LINKED_DEATH_UPKEEP_CAPABILITY),
        ("creature.dies.self", GRANTED_DEATH_ROLE_RETURN_CAPABILITY),
    )
    if not permanent_graveyard and not revival and not any(
        event_has_capability_subscriber(host, event, capability) for event, capability in watched
    ):
        return context
    if revival:
        context = capture_revival_death_count(context, occurrence)
    return {**context, DEATH_BATTLEFIELD_ARRIVALS: committed_graveyard_arrivals(occurrence, parts)}


def committed_graveyard_arrivals(occurrence, parts):
    """Pin the physical graveyard objects of one committed departure."""
    group = occurrence.composition
    if group is None:
        arrivals = (occurrence,)
    else:
        if (not group.permanent_departure or len(parts) != group.member_count or any(
            part.composition is None or not part.composition.physical_arrival
            or part.composition.parent != group.parent for part in parts
        )):
            raise ValueError("Death return requires the complete committed composition departures")
        arrivals = parts
    return [
        DeathBattlefieldArrival(part.object_id, part.card_ref, part.zone_change_counter, part.owner).to_dict()
        for part in arrivals if part.destination == "graveyard" and part.card_object and not part.token
    ]


def decode_death_battlefield_arrivals(value: object) -> tuple[DeathBattlefieldArrival, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("Death return requires its sealed arrivals")
    arrivals = tuple(DeathBattlefieldArrival.from_dict(row) for row in value)
    if len({row.object_id for row in arrivals}) != len(arrivals):
        raise ValueError("Death return cannot repeat a physical arrival")
    return arrivals


def validate_death_battlefield_return(effect) -> tuple[DeathBattlefieldArrival, ...]:
    required = set(death_battlefield_return_effect())
    allowed = required | {"_replacement_selections", "_runtime_source", "_aura_target_refs"}
    if (not required <= set(effect) <= allowed or not is_death_battlefield_return(effect)
            or type(effect["schema_version"]) is not int
            or effect["schema_version"] != FLYING_EVENT_BATTLEFIELD_RETURN_VERSION
            or type(effect["source"]) is not str or not effect["source"] or effect["source"].startswith("$")
            or not isinstance(effect.get("_replacement_selections", ()), (tuple, list))):
        raise ValueError("Death return requires one closed resolving instruction")
    arrivals = decode_death_battlefield_arrivals(effect[DEATH_BATTLEFIELD_ARRIVALS])
    selections = effect.get("_aura_target_refs", {})
    if (not isinstance(selections, Mapping) or not set(selections) <= {row.object_id for row in arrivals}
            or any(type(ref) is not str or not ref for ref in selections.values())):
        raise ValueError("Death return Aura choices require their original public cards")
    return arrivals


def death_battlefield_return_runtime_dependencies(program) -> tuple[str, ...]:
    from .effect_shape_walkers import nested_effects
    from .opposing_death_delayed_return import (
        OPPOSING_DEATH_DELAYED_RETURN_MECHANIC, is_opposing_death_delayed_return,
        opposing_death_delayed_return_runtime_dependencies,
    )

    from .granted_death_role_return import granted_death_role_return_dependencies

    from .revival_return import revival_return_runtime_dependencies

    if revival := revival_return_runtime_dependencies(program):
        return revival
    required = granted_death_role_return_dependencies(program)
    if required:
        return required
    from .granted_death_treasure import granted_death_treasure_runtime_dependencies
    if required := granted_death_treasure_runtime_dependencies(program):
        return required

    from .granted_death_role_return import granted_self_death_return_dependencies

    effects = tuple(nested_effects((program.effects, program.handlers)))
    required = granted_self_death_return_dependencies(program, effects)
    if required:
        return required
    if OPPOSING_DEATH_DELAYED_RETURN_MECHANIC in program.coverage or any(
        is_opposing_death_delayed_return(effect) for effect in effects
    ):
        return opposing_death_delayed_return_runtime_dependencies(program)
    if not any(is_death_battlefield_return(effect) for effect in effects):
        return ()
    if (program.active_zone != "battlefield" or program.event != "creature.dies" or program.target_schema is not None
            or program.handlers or tuple(program.effects) != (death_battlefield_return_effect(),)
            or FrozenMap(program.event_condition or {}) != FrozenMap(controlled_nonflying_death_condition())):
        raise ValueError("Death return requires its exact controlled nonflying creature death")
    return DEATH_BATTLEFIELD_RETURN_DEPENDENCIES


def apply_death_battlefield_return(host, effect, *, actor, reason):
    """Resolve all eligible arrivals in one battlefield-entry transaction."""
    from ..zone_transitions import ZoneTransitionOwner

    try:
        arrivals = validate_death_battlefield_return(effect)
        source = ResolutionEffectSource.from_effect(effect)
        item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
        if (item is None or item.controller != actor or item.context.get("event") != "creature.dies"
                or item.context.get("currently_resolving") is not True or effect["source"] != item.ref
                or decode_death_battlefield_arrivals(item.context.get(DEATH_BATTLEFIELD_ARRIVALS)) != arrivals):
            raise ValueError("Death return lost the resolving trigger and its original arrivals")
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    cards = tuple(
        card for row in arrivals
        if row.matches(card := host.state.cards.get(row.object_id)) and card.owner in host.active_seats
        and type_parts(str(host._effective_card_data(card).get("type_line") or ""))[0] & PERMANENT_CARD_TYPES
    )
    if not cards:
        return ()
    counters = {
        card.object_id: (EffectEntryCounter("flying", 1, card.owner, item.ref, "122.6a"),) for card in cards
    }
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        tuple((card.object_id, "battlefield") for card in cards), reason=reason, tapped=False,
        destination_controllers={card.object_id: card.owner for card in cards},
        putting_players={card.object_id: actor for card in cards},
        effect_entry_counters=counters,
        replacement_selections=tuple(effect.get("_replacement_selections", ())),
        aura_target_refs=effect.get("_aura_target_refs"),
    )
    return tuple(card.ref for card in moved if card.zone == "battlefield")
