from __future__ import annotations

import re
from ..damage_prevention_permission import (StaticUnpreventableDamageSpec,UNPREVENTABLE_HANDLER,
    UNPREVENTABLE_EVENT,UNPREVENTABLE_CAPABILITY)


def static_damage_unpreventability_handler(text):
    frames = ((r"Damage can't be prevented\.?",'global'),
        (r"Damage (?:that would be )?dealt by this creature can't be prevented\.?",'self'),
        (r"Combat damage (?:that would be )?dealt by creatures you control can't be prevented\.?",'controller_creatures_combat'))
    for pattern,scope in frames:
        if re.fullmatch(pattern,text.strip(),re.I):
            return (f'static-unpreventable-damage-{scope}-v1',
                {'handler_id':UNPREVENTABLE_HANDLER,'schema_version':1,'event':UNPREVENTABLE_EVENT,
                 'rule':StaticUnpreventableDamageSpec(scope).to_dict()},UNPREVENTABLE_CAPABILITY)
    return None
