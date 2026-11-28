"""One affected-player order for the kinds in a single counter placement."""

from collections import defaultdict
from dataclasses import replace

from .model import ReplacementEffectError


COUNTER_INSTRUCTION_GROUP = "counter_instruction_group"


def counter_instruction_controllers(effects):
    return {
        effect.conditions["target_controller"]["eq"]
        for effect in effects
        if effect.counter_instruction_order_version == 1
    }


def _counter_group_subject(event, controllers, *, entry_logical_id=None):
    if (event.kind != "counter.place" or event.children
            or event.payload.get("target_kind") != "permanent"
            or event.payload.get("target_zone") != "battlefield"
            or event.payload.get("target_controller") not in controllers
            or event.affected_object is None
            or event.affected_object.controller != event.payload.get("target_controller")):
        return None
    logical = entry_logical_id if entry_logical_id is not None else event.payload.get("target_logical_object_id")
    if not isinstance(logical, str) or not logical:
        return None
    return event.affected_object.object_id, logical, event.payload.get("target_controller")


def _distinct_kinds(events):
    names = tuple(event.payload.get("counter_name") for event in events)
    return len(names) >= 2 and len(set(names)) == len(names)


def _direct_scope(event, controllers):
    subject = _counter_group_subject(event, controllers)
    if subject is None:
        return None
    return subject, event.payload.get("placing_player"), event.payload.get("source")


def direct_counter_instruction_indices(events, current, effects):
    anchor = current.payload.get(COUNTER_INSTRUCTION_GROUP)
    if anchor is None:
        return ()
    controllers = counter_instruction_controllers(effects)
    intended = _direct_scope(current, controllers)
    members = tuple((index, event) for index, event in enumerate(events)
                    if event.payload.get(COUNTER_INSTRUCTION_GROUP) == anchor)
    if (type(anchor) is not str or not anchor or intended is None
            or not members or members[0][1].event_id != anchor
            or not _distinct_kinds(tuple(event for _, event in members))
            or any(_direct_scope(event, controllers) != intended for _, event in members)):
        raise ReplacementEffectError("Counter instruction group changed its original placement scope")
    return tuple(index for index, _ in members)


def bind_counter_instruction_groups(events, effects):
    """Seal the group on the immutable leaves of a single placement instruction."""
    controllers = counter_instruction_controllers(effects)
    if not controllers:
        return tuple(events)
    groups = defaultdict(list)
    for index, event in enumerate(events):
        scope = _direct_scope(event, controllers)
        if scope is not None:
            groups[scope].append(index)
    result = list(events)
    for indices in groups.values():
        if len(indices) < 2:
            continue
        if not _distinct_kinds(tuple(events[index] for index in indices)):
            continue
        anchor = events[indices[0]].event_id
        for index in indices:
            event = events[index]
            result[index] = replace(event, payload={**event.payload, COUNTER_INSTRUCTION_GROUP: anchor})
    return tuple(result)


def entry_counter_instruction_indices(parent, current, effects):
    """All counter leaves attached to one prospective battlefield entry."""
    if parent.kind != "zone.change" or parent.payload.get("destination") != "battlefield":
        return ()
    controllers = counter_instruction_controllers(effects)
    logical_id = parent.payload.get("logical_object_id")
    if type(logical_id) is not str or not logical_id or parent.affected_object is None:
        return ()

    def entry_subject(child):
        if child.affected_object != parent.affected_object or child.payload.get("follows_zone_destination") is not True:
            return None
        return _counter_group_subject(child, controllers, entry_logical_id=logical_id)

    subject = entry_subject(current)
    if subject is None:
        return ()
    members = tuple((index, child) for index, child in enumerate(parent.children) if entry_subject(child) == subject)
    if not _distinct_kinds(tuple(child for _, child in members)):
        return ()
    return tuple(index for index, _ in members)


def combine_counter_instruction_choices(representative, members):
    if len(members) < 2:
        return representative
    priority = min(choice.replacement_class for _, choice in members)
    eligible = tuple((path, choice) for path, choice in members if choice.replacement_class == priority)
    chooser = representative.choice.chooser
    if any(choice.chooser != chooser for _, choice in eligible):
        raise ReplacementEffectError("Counter instruction members must have one affected player")
    options = tuple(sorted({effect for _, choice in eligible for effect in choice.options}))
    optional = tuple(sorted({effect for _, choice in eligible for effect in choice.optional_options}))
    return replace(
        representative,
        choice=replace(representative.choice, options=options, optional_options=optional, replacement_class=priority),
        counter_instruction_members=eligible,
    )
