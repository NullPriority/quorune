"""One controlled subtype damage occurrence per final committed batch."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..creature_subtypes import canonical_creature_subtype
from ..hand_entry_queries import DAMAGE_PERMANENT_HAND_KIND, damage_permanent_hand_query
from ..replacement.immutable import thaw_value
from .token_creation_capability_shapes import fixed_token_creation_node_capabilities


EVENT = "damage.dealt.controlled_subtype_opponents"
CAPABILITY = "trigger.event.controlled_subtype_damaged_opponents"
MECHANIC = "controlled-subtype-opponents-damage-batch"
TEMPLATE = "controlled-subtype-opponents-damage-batch-v1"
VARIANT = "controlled-subtype-opponents-damage-treasure"
AMOUNT_REFERENCE = "$context.amount"
COMBAT_EVENT = "damage.dealt.controlled_subtype_combat_player"
COMBAT_CAPABILITY = "trigger.event.controlled_subtype_combat_damage_player"
COMBAT_MECHANIC = "controlled-subtype-combat-player-damage-batch"
COMBAT_TEMPLATE = "controlled-subtype-combat-player-damage-v1"
COMBAT_VARIANT = "controlled-subtype-combat-player-permanent-from-hand"


def combat_subtype_hand_entry_effect():
    return {"op": "put_card_from_hand", "player": "$controller",
            "query": damage_permanent_hand_query(), "tapped": False}


def controlled_subtype_damage_condition(subtype):
    return {"all": [
        {"field": "source_controller", "op": "eq", "value": "$source.controller"},
        {"field": "source_subtypes", "op": "contains_any", "value": [subtype]},
    ]}


def controlled_subtype_combat_damage_condition(subtype):
    return {"all": [
        *controlled_subtype_damage_condition(subtype)["all"],
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
    ]}


def _condition_subtype(condition, *, combat=False):
    condition = thaw_value(condition)
    try:
        subtype = condition["all"][1]["value"][0]
    except (TypeError, ValueError, KeyError, IndexError) as exc:
        raise ValueError("Grouped damage requires its exact controller and subtype") from exc
    expected = controlled_subtype_combat_damage_condition if combat else controlled_subtype_damage_condition
    if (type(subtype) is not str or canonical_creature_subtype(subtype) != subtype
            or condition != expected(subtype)):
        raise ValueError("Grouped damage requires its exact controller and subtype")
    return subtype


def controlled_subtype_damage_node_capabilities(*, effects, target_schema, mechanics):
    if COMBAT_MECHANIC in mechanics:
        if (target_schema is not None or len(effects) != 1
                or thaw_value(effects[0]) != combat_subtype_hand_entry_effect()
                or effects[0].get("tapped") is not False
                or type(effects[0]["query"]["schema_version"]) is not int):
            return ()
        return (COMBAT_CAPABILITY, "trigger.event.normalized_damage", "trigger.effect.fixed_event",
                "trigger.placement.apnap", "zone.move.fixed_private_hand_choice",
                "zone.change.destination_replacement", "attachment.aura.typed_restriction")
    if MECHANIC not in mechanics:
        return None
    if target_schema is not None or len(effects) != 1 or not isinstance(effects[0], Mapping):
        return ()
    effect = effects[0]
    if (effect.get("op") != "create_token" or effect.get("name") != "Treasure Token"
            or effect.get("controller") != "$controller" or effect.get("quantity") != AMOUNT_REFERENCE):
        return ()
    token = fixed_token_creation_node_capabilities(
        effects=({**thaw_value(effect), "quantity": 1},), target_schema=None,
        mechanic_ids={"cr-111-tokens"},
    )
    if not token:
        return ()
    return (CAPABILITY, "trigger.event.normalized_damage", "trigger.effect.fixed_event",
            "trigger.placement.apnap", *token)


def controlled_subtype_damage_runtime_dependencies(program):
    if program.event == COMBAT_EVENT or COMBAT_MECHANIC in program.coverage or any(
        isinstance(effect.get("query"), Mapping) and effect["query"].get("kind") == DAMAGE_PERMANENT_HAND_KIND
        for effect in program.effects
    ):
        event = COMBAT_EVENT
    elif program.event == EVENT or MECHANIC in program.coverage:
        event = EVENT
    else:
        return None
    required = controlled_subtype_damage_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanics=program.coverage,
    )
    if (not required or (program.event, program.active_zone) != (event, "battlefield")
            or (COMBAT_MECHANIC in program.coverage) != (event == COMBAT_EVENT)
            or program.handlers or program.cost_schema is not None or program.trigger_limit is not None):
        raise ValueError("Grouped damage requires its complete battlefield trigger")
    _condition_subtype(program.event_condition, combat=event == COMBAT_EVENT)
    return required


def is_closed_controlled_subtype_damage_program(program):
    try:
        required = controlled_subtype_damage_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


@dataclass(frozen=True, slots=True)
class ControlledSubtypeDamageOccurrence:
    event_id: str
    controller: str
    subtype: str
    hits: tuple[tuple[str, str, int], ...]

    def __post_init__(self):
        if (type(self.event_id) is not str or not self.event_id
                or type(self.controller) is not str or not self.controller
                or type(self.subtype) is not str or canonical_creature_subtype(self.subtype) != self.subtype
                or not isinstance(self.hits, tuple) or not self.hits):
            raise ValueError("Grouped damage requires one qualifying final occurrence")
        for hit in self.hits:
            if (not isinstance(hit, tuple) or len(hit) != 3
                    or any(type(value) is not str or not value for value in hit[:2])
                    or hit[1] == self.controller or type(hit[2]) is not int or hit[2] < 1):
                raise ValueError("Grouped damage requires positive opposing-player damage")

    @property
    def opponents(self):
        return tuple(sorted({hit[1] for hit in self.hits}))

    def semantic_context(self):
        return {
            "event_id": self.event_id, "source_controller": self.controller, "source_subtypes": [self.subtype],
            "amount": len(self.opponents), "damaged_opponents": list(self.opponents),
            "damage_hits": [list(hit) for hit in self.hits],
        }


def controlled_subtype_damage_occurrences(events, *, event_id, active_seats, wanted_subtypes):
    groups = {}
    for event in events:
        if (not event.was_dealt or event.target_kind != "player" or event.source_zone != "battlefield"
                or event.source_controller not in active_seats or event.target not in active_seats
                or event.target == event.source_controller):
            continue
        hit = (event.source_logical_object_id, event.target, event.dealt_amount)
        for subtype in wanted_subtypes.intersection(event.source_subtypes):
            groups.setdefault((event.source_controller, subtype), []).append(hit)
    return tuple(
        ControlledSubtypeDamageOccurrence(event_id, controller, subtype, tuple(sorted(rows)))
        for (controller, subtype), rows in sorted(groups.items())
    )


def controlled_subtype_combat_occurrences(events, *, event_id, active_seats, wanted_subtypes):
    amounts = {}
    for event in events:
        if (not event.combat or not event.was_dealt or event.target_kind != "player"
                or event.source_zone != "battlefield" or event.source_controller not in active_seats
                or event.target not in active_seats):
            continue
        for subtype in wanted_subtypes.intersection(event.source_subtypes):
            key = (event.source_controller, subtype, event.target)
            amounts[key] = amounts.get(key, 0) + event.dealt_amount
    return tuple(
        {"event_id": f"{event_id}:subtype-combat:{controller}:{subtype}:{defender}",
         "source_controller": controller, "source_subtypes": [subtype], "combat": True,
         "target_kind": "player", "target": defender, "amount": amount}
        for (controller, subtype, defender), amount in sorted(amounts.items())
    )


def dispatch_controlled_subtype_damage_batches(host, result, *, sources, source_zones, trigger_batch):
    registry = getattr(host, "semantics", None)
    if registry is None:
        return
    for event in (EVENT, COMBAT_EVENT):
        contexts = _current_subtype_damage_contexts(host, result, registry, event)
        for context in contexts:
            host._dispatch_semantic_event(
                event, context, sources=sources, source_zones=source_zones, trigger_batch=trigger_batch,
            )


def _current_subtype_damage_contexts(host, result, registry, event):
    wanted = frozenset(
        _condition_subtype(program.event_condition, combat=event == COMBAT_EVENT)
        for program in registry.programs_for_event(event)
        if host.semantic_program_is_current_trusted(program) and is_closed_controlled_subtype_damage_program(program)
    )
    if not wanted:
        return ()
    options = {"event_id": result.event_batch_id, "active_seats": frozenset(host.active_seats),
               "wanted_subtypes": wanted}
    if event == COMBAT_EVENT:
        return controlled_subtype_combat_occurrences(result.events, **options)
    return tuple(occurrence.semantic_context() for occurrence in controlled_subtype_damage_occurrences(
        result.events, **options,
    ))
