from __future__ import annotations

"""Current giver-owned attack requirements over one enchanted player's creatures."""

from ..attachments import current_attached_player_seat
from ..enchanted_player_model import EnchantedPlayerAttackRequirementSpec
from ..combat_constraints import DeclarationRequirement


def enchanted_player_attacker_requirements(host,attacker,*,error_type):
    if attacker.zone!='battlefield' or attacker.phased_out:return ()
    sources=[source for source in sorted(host.state.cards.values(),key=lambda card:card.ref)
        if current_attached_player_seat(source,host.state.players)==attacker.controller]
    if not sources or 'creature' not in host._type_parts(host._effective_card_data(attacker)['type_line'])[0]:return ()
    result=[]
    for source in sources:
        for index,spec in enumerate(host._effective_ability_fragments(source,error_type=error_type)):
            if isinstance(spec,EnchantedPlayerAttackRequirementSpec):
                result.append(DeclarationRequirement(
                    requirement_id=f'attack:{attacker.ref}:enchanted-player:{source.logical_object_id}:{index}',
                    kind='choose',variable=attacker.ref,label='This creature attacks this combat if able.'))
    return tuple(result)
