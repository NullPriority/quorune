"""Closed paired Champion instruction shapes, independent of card identity."""
from __future__ import annotations

from ..champion_model import CHAMPION_CAPABILITY, CHAMPION_CHOICE_OPERATION, ChampionSpec
from ..replacement.immutable import thaw_value


def champion_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or 'champion' not in mechanic_ids or len(effects)!=1:
        return ()
    effect=effects[0]
    entry=effect.get('op')==CHAMPION_CHOICE_OPERATION
    if set(effect)!=({'op','ability_pair','spec'} if entry else {'op','ability_pair'}):
        return ()
    if effect.get('op') not in {CHAMPION_CHOICE_OPERATION,'return_champion_exile'}:
        return ()
    pair = effect.get('ability_pair')
    if (type(pair) is not str or not pair.startswith('champion:') or pair == 'champion:'
            or pair != pair.strip()):
        return ()
    if entry:
        try:ChampionSpec.from_dict(thaw_value(effect['spec']))
        except (ValueError,TypeError):return ()
    return (CHAMPION_CAPABILITY,)


def is_closed_champion_program(program):
    required=champion_node_capabilities(effects=program.effects,target_schema=program.target_schema,
                                        mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies) and program.event in {
        'permanent.enter.self' if program.effects[0]['op']==CHAMPION_CHOICE_OPERATION else 'permanent.leave.self'}
