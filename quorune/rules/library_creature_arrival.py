"""Group committed library-to-graveyard creature arrivals by owner."""

from ..replacement.immutable import FrozenMap
from ..zone_trigger_events import sealed_public_characteristic_facts
from .effect_shape_walkers import nested_effects


EVENT = "card.library_creature_graveyard_arrival.v1"
VARIANT = "one_or_more_owned_library_creature_graveyard_arrivals"
OPERATION = "choose_library_arrival_to_battlefield"
MECHANIC = "grouped-library-creature-arrival-return"
CAPABILITY = "trigger.event.owned_library_creature_arrival_group"
DEPENDENCIES = (CAPABILITY, "trigger.event.normalized_zone_change", "trigger.effect.fixed_event",
                "trigger.placement.apnap", "zone.change.destination_replacement", "variant.commander.zone_return")
EVENT_FIELD = "library_creature_arrivals"


def arrival_condition():
    return {"field":"owner","op":"eq","value":"$source.controller"}


def arrival_effect():
    return {"op":OPERATION,"schema_version":1,"player":"$controller",
            "arrivals":f"$context.{EVENT_FIELD}"}


def arrival_node_capabilities(effects, target_schema, mechanics, cost_schema):
    if MECHANIC not in mechanics and not any(effect.get("op")==OPERATION for effect in effects):
        return None
    if (target_schema is not None or cost_schema is not None or len(effects)!=1
            or type(effects[0].get("schema_version")) is not int
            or FrozenMap(effects[0]) != FrozenMap(arrival_effect())):
        return ()
    return DEPENDENCIES


def arrival_program_dependencies(program):
    if (program.event != EVENT and MECHANIC not in program.coverage
            and not any(value.get("op")==OPERATION for value in nested_effects((program.effects,program.handlers)))):
        return ()
    owned=arrival_node_capabilities(program.effects,program.target_schema,program.coverage,program.cost_schema)
    if (not owned or not program.ability_id.startswith("trigger:") or program.handlers
            or (program.event,program.active_zone)!=(EVENT,"battlefield")
            or "one_or_more_event_batch" not in program.coverage
            or FrozenMap(program.event_condition or {}) != FrozenMap(arrival_condition())):
        raise ValueError("A library arrival return requires its original grouped battlefield trigger")
    return owned


def dispatch_grouped_library_creature_arrivals(host, occurrences, *, trigger_batch):
    from .event_subscriptions import event_has_capability_subscriber

    groups = {}
    for occurrence in occurrences:
        if (occurrence.origin != "library" or occurrence.destination != "graveyard"
                or not occurrence.card_object or occurrence.token):
            continue
        if "creature" not in sealed_public_characteristic_facts(occurrence.current_characteristics)["types"]:
            continue
        groups.setdefault(occurrence.owner, []).append({
            "ref": occurrence.card_ref, "logical_id": occurrence.current_logical_object_id,
            "counter": occurrence.zone_change_counter,
        })
    if not groups or not event_has_capability_subscriber(host, EVENT, CAPABILITY):
        return
    for owner in host.apnap_order():
        if group := groups.get(owner):
            host._dispatch_semantic_event(
                EVENT, {"owner":owner, EVENT_FIELD:group}, trigger_batch=trigger_batch,
            )
