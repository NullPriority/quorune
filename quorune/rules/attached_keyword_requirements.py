from __future__ import annotations

"""Descriptor-owned capability minima for executable attached keyword grants."""

from ..ability_fragments import (ability_fragment_from_dict,ProtectionSpec,ToxicSpec,
    CombatKeywordTriggerSpec,SpellCastKeywordTriggerSpec)
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES,TRIGGERED_KEYWORD_GRANT_CAPABILITIES


def attached_keyword_runtime_dependencies(program):
    from ..semantic_runtime.attached_continuous import AttachedFixedCharacteristicsHandler
    handler=AttachedFixedCharacteristicsHandler();required=set()
    for descriptor in program.handlers:
        if descriptor.get('handler_id')!=handler.handler_id:continue
        node=handler.validate(descriptor)
        for keyword in (*node.add_abilities,*node.remove_abilities):
            required.update(FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES.get(keyword,()))
        for value in node.add_ability_fragments:
            fragment=ability_fragment_from_dict(value)
            owner=getattr(fragment,'capability_id',None)
            if isinstance(owner,str):required.add(owner)
            if isinstance(fragment,ProtectionSpec):required.add('protection.typed.debt')
            elif isinstance(fragment,ToxicSpec):required.add('damage.result.toxic')
            elif isinstance(fragment,(CombatKeywordTriggerSpec,SpellCastKeywordTriggerSpec)):
                label=fragment.kind.value.replace('_',' ').title()
                owner=TRIGGERED_KEYWORD_GRANT_CAPABILITIES.get(label)
                if owner is not None:required.add(owner)
                elif label=='Bushido':required.add('combat.trigger.bushido')
    return tuple(sorted(required))
