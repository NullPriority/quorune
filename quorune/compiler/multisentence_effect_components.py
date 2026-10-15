from __future__ import annotations

"""Compose independently exact leaves whose own grammar spans sentences."""

import re
from .closed_effect_programs import ClosedEffectProgramTemplate, _NESTED_SEQUENCE_MECHANICS, _top_level_positions


def multisentence_effect_program(text: str, *, compile_component):
    normalized = text.strip()
    positions = _top_level_positions(normalized, '. ')
    if positions is None or not 2 <= len(positions) <= 7:
        return None
    boundaries = (0, *(position + 2 for position in positions), len(normalized))
    cache = {}

    def paths(start, components=()):
        if start == len(boundaries) - 1:
            if len(components) >= 2:
                yield components
            return
        if len(components) == 4:
            return
        for end in range(start + 1, len(boundaries)):
            key = (start, end)
            if key not in cache:
                clause = re.sub(r'^Then\s+', '', normalized[boundaries[start]:boundaries[end]].strip(), flags=re.I)
                cache[key] = compile_component(clause)
            component = cache[key]
            if component[0] is None or not component[1] or not component[3] or 'closed-effect-program' in component[3]:
                continue
            yield from paths(end, (*components, component))

    for components in paths(0):
        effects = tuple(effect for component in components for effect in component[1])
        if not 2 <= len(effects) <= 8:
            continue
        schemas = [component[2] for component in components if component[2] is not None]
        if len(schemas) > 1 or schemas and schemas[0].get('count') != 1:
            continue
        mechanics = tuple(dict.fromkeys(('closed-effect-program', *(mechanic for component in components
            for mechanic in component[3] if mechanic not in _NESTED_SEQUENCE_MECHANICS))))
        from ..rules.closed_effect_program_shapes import closed_effect_program_node_capabilities
        schema = schemas[0] if schemas else None
        if not closed_effect_program_node_capabilities(effects=effects, target_schema=schema, mechanic_ids=mechanics):
            continue
        return ClosedEffectProgramTemplate(tuple(component[0] for component in components),
            tuple(len(component[1]) for component in components), effects, schema, mechanics).compiled()
    return None
