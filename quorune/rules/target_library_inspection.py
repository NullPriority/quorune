"""Authorize exactly the new operation's current targeted top window."""
from ..target_library_inspection_model import (
    TARGET_LIBRARY_INSPECTION_OPERATION, TARGET_LIBRARY_INSPECTION_MECHANIC,
    TARGET_LIBRARY_INSPECTION_CAPABILITY, TARGET_LIBRARY_INSPECTION_FIELDS, decode_target_library_inspection,
)
from ..replacement.immutable import thaw_value


def target_library_inspection_instruction_capabilities(effect):
    spec = decode_target_library_inspection(effect)
    return (TARGET_LIBRARY_INSPECTION_CAPABILITY, "target.revalidate_resolution",
            *(("zone.change.destination_replacement",) if spec.mode != "look" else ()))


def target_library_inspection_node_capabilities(*, effects, target_schema, mechanic_ids):
    mechanics = set(mechanic_ids)
    if TARGET_LIBRARY_INSPECTION_MECHANIC not in mechanics or not 1 <= len(effects) <= 2:return ()
    try:
        spec = decode_target_library_inspection(effects[0]);owners = set(target_library_inspection_instruction_capabilities(effects[0]))
    except (TypeError, ValueError, KeyError):return ()
    if thaw_value(target_schema) != spec.target_schema():return ()
    if len(effects) == 2:
        from .delayed_draw_capability_shapes import fixed_next_turn_draw_node_capabilities
        followup = fixed_next_turn_draw_node_capabilities(effects=effects[1:], target_schema=None, mechanic_ids=mechanics)
        if not followup or spec.mode != "look":return ()
        owners.update(followup)
    return tuple(sorted(owners))


def is_closed_target_library_inspection_program(program):
    owners = target_library_inspection_node_capabilities(effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(owners) and set(owners) <= set(program.capability_dependencies)


def scoped_target_library_inspection_refs(host, actor, effect):
    if effect.get("op") != TARGET_LIBRARY_INSPECTION_OPERATION:return ()
    spec = decode_target_library_inspection({key:effect[key] for key in TARGET_LIBRARY_INSPECTION_FIELDS}, actor=actor)
    target = effect["target"]
    if target not in host.active_seats:return ()
    return tuple(host.state.cards[oid].ref for oid in reversed(host.state.players[target].zones["library"]))[:spec.count]


def scoped_target_library_inspection_libraries(host, actor, effect):
    if effect.get("op") != TARGET_LIBRARY_INSPECTION_OPERATION:
        return {}
    refs = scoped_target_library_inspection_refs(host, actor, effect)
    target = effect["target"]
    return {target: tuple(reversed(refs))} if target in host.active_seats else {}
