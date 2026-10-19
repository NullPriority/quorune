from __future__ import annotations

"""Share immutable entry-object reservations across one simultaneous boundary."""

from dataclasses import replace


def share_entry_reservations(events):
    groups={}
    for event in events:
        scope=event.entry_scope
        if scope is None:continue
        key=(frozenset(scope.entering_objects),frozenset(scope.entering_from_library))
        ids=groups.setdefault(key,[])
        ids.extend(v for v in scope.reserved_zone_changes if v not in ids)

    def apply(event,inherited=None):
        scope=event.entry_scope or inherited
        if scope is None:return event
        ids=groups.get((frozenset(scope.entering_objects),frozenset(scope.entering_from_library)),())
        if not ids:return event
        shared=replace(scope,reserved_zone_changes=tuple(ids))
        children=tuple(apply(child,shared) for child in event.children)
        return replace(event,entry_scope=shared,children=children)
    return tuple(apply(event) for event in events)
