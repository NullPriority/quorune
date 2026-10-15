from __future__ import annotations

from ..manifest import MANIFEST_CAPABILITY, MANIFEST_DREAD_CAPABILITY


def manifest_node_capabilities(*, effects, target_schema, mechanics):
    if target_schema is not None or not effects or 'manifest' not in mechanics:
        return ()
    if any(set(effect) != {'op', 'player', 'mode'} or effect['op'] != 'manifest_card'
            or effect['player'] != '$controller' or effect['mode'] not in {'top', 'dread'} for effect in effects):
        return ()
    deps = {MANIFEST_CAPABILITY}
    if any(effect['mode'] == 'dread' for effect in effects):
        deps.add(MANIFEST_DREAD_CAPABILITY)
    if 'cr-603-handling-triggered-abilities' in mechanics:
        deps.add('trigger.placement.apnap')
    if 'trigger-event-normalized-zone-change' in mechanics:
        deps.add('trigger.event.normalized_zone_change')
    return tuple(sorted(deps))
