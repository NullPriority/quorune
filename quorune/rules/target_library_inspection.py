"""Authorize exactly the new operation's current targeted top window."""
from ..target_library_inspection_model import (
    TARGET_LIBRARY_INSPECTION_OPERATION, TARGET_LIBRARY_INSPECTION_MECHANIC,
    TARGET_LIBRARY_INSPECTION_CAPABILITY, TARGET_LIBRARY_INSPECTION_FIELDS, decode_target_library_inspection,
)
from ..replacement.immutable import thaw_value
from ..private_opponent_library_cast_model import (
    DAMAGE_GRAVE_ORDER, DAMAGE_SELECTED, damage_library_cast_selection, private_opponent_library_cast_selection,
    uses_damage_library_cast_selection, uses_private_opponent_library_cast_selection,
    validate_private_opponent_library_cast_selection,
)


_PRIVATE_CAST_CONTINUATION_FIELDS = {
    "_choice_actor", "_effective_minimum", "_effective_maximum", "_eligible_groups",
    "_looked_objects", "_stack_label",
}


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
    if uses_private_opponent_library_cast_selection(effect):
        target = effect.get("player")
        if type(target) is not str or target == actor or target not in host.active_seats:
            return ()
        damage_cast = uses_damage_library_cast_selection(effect)
        fields = damage_library_cast_selection(target) if damage_cast else private_opponent_library_cast_selection(target)
        continuation_fields = set(effect) - set(fields)
        if damage_cast:
            if DAMAGE_GRAVE_ORDER in continuation_fields and DAMAGE_SELECTED not in continuation_fields:
                raise ValueError("Private graveyard ordering lost its selected card")
            extra_fields = continuation_fields & {DAMAGE_GRAVE_ORDER, DAMAGE_SELECTED}
        else:
            extra_fields = set()
        if continuation_fields and (
            continuation_fields - extra_fields != _PRIVATE_CAST_CONTINUATION_FIELDS or effect.get("_choice_actor") != actor
        ):
            raise ValueError("Private opponent library selection has invalid continuation fields")
        view = {key: value for key, value in effect.items() if key not in continuation_fields}
        validate_private_opponent_library_cast_selection(view, player=target)
        library = host.state.players[target].zones["library"]
        return tuple(host.state.cards[oid].ref for oid in reversed(library[-fields["look_count"]:]))
    if effect.get("op") != TARGET_LIBRARY_INSPECTION_OPERATION:return ()
    spec = decode_target_library_inspection({key:effect[key] for key in TARGET_LIBRARY_INSPECTION_FIELDS}, actor=actor)
    target = effect["target"]
    if target not in host.active_seats:return ()
    return tuple(host.state.cards[oid].ref for oid in reversed(host.state.players[target].zones["library"]))[:spec.count]


def scoped_target_library_inspection_libraries(host, actor, effect):
    private_cast = uses_private_opponent_library_cast_selection(effect)
    if effect.get("op") != TARGET_LIBRARY_INSPECTION_OPERATION and not private_cast:
        return {}
    refs = scoped_target_library_inspection_refs(host, actor, effect)
    target = effect.get("player") if private_cast else effect["target"]
    return {target: tuple(reversed(refs))} if target in host.active_seats else {}
