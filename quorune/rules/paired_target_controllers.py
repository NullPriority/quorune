"""Current and last battlefield controllers for one pair of targets."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..turn_history import current_turn_history_events
from .fixed_homogeneous_target_set_capability_shapes import fixed_homogeneous_target_set_node_capabilities


PAIRED_CONTROLLER_CAPABILITY = "target.set.pair_of_distinct_controllers"


def uses_paired_controllers(group):
    return group.distinct_controllers and not group.retain_controller


def paired_controller_target_dependencies(effects, target_schema, mechanics):
    from ..compiler.direct_target import permanent_target_schema

    expected = {**permanent_target_schema(types_any=("creature",)), "count": 2, "distinct_controllers": True}
    if (not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or target_schema.get("distinct_controllers") is not True or FrozenMap(target_schema) != FrozenMap(expected)
            or len(effects) != 1 or effects[0].get("op") != "return_permanent_targets_to_owner_hand"):
        return ()
    plain_schema = {key: value for key, value in target_schema.items() if key != "distinct_controllers"}
    required = fixed_homogeneous_target_set_node_capabilities(
        effects=effects, target_schema=plain_schema, mechanic_ids=mechanics,
    )
    return (PAIRED_CONTROLLER_CAPABILITY, *required) if required else ()


def controller_of_target_incarnation(host, ref, snapshot):
    object_id = snapshot.get("object_id")
    incarnation = snapshot.get("zone_change_counter")
    if type(object_id) is not str or type(incarnation) is not int or snapshot.get("zone") != "battlefield":
        return None
    state = host.state
    card = state.cards.get(object_id)
    if card is not None and card.ref == ref and card.zone == "battlefield" and card.zone_change_counter == incarnation:
        if card.phased_out:
            return card.phasing_record.return_seat if card.phasing_record is not None else None
        return card.controller
    # A pending spell cannot carry its targets past the end of the current turn.
    departures = current_turn_history_events(state.turn_history, turn_sequence=state.turn_sequence, kind="permanent_left")
    identity = f"{object_id}@{incarnation}"
    return next((event.actor for event in reversed(departures) if event.object_incarnation == identity), None)


def paired_controller_resolution_rejections(host, plan, announced, current, snapshots):
    rejected = []
    for group in plan.groups:
        group_id = group.group_id
        legal = tuple(ref for ref in current.get(group_id, ()) if ref is not None)
        if not legal or not uses_paired_controllers(group):
            continue
        refs = announced.get(group_id, ())
        controllers = tuple(controller_of_target_incarnation(host, ref, snapshots.get(ref, {})) for ref in refs)
        if len(controllers) != 2 or None in controllers or controllers[0] == controllers[1]:
            rejected.extend((group_id, ref) for ref in legal)
    return rejected
