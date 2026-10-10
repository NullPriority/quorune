from __future__ import annotations

"""Closed optional draw instruction followed by mandatory controller discard."""

from typing import Mapping
from .affected_player_discard_capability_shapes import fixed_affected_player_discard_node_capabilities


def optional_draw_discard_is_closed(effects,*,player=None):
    if not isinstance(effects,(list,tuple)) or len(effects)!=2 or any(not isinstance(e,Mapping) for e in effects):return False
    draw,discard=effects
    if set(draw)!={'op','player','count','private'} or draw['op']!='draw' or draw['private'] is not True or type(draw['count']) is not int or not 1<=draw['count']<=3:return False
    if player is not None and draw['player']!=player:return False
    players=discard.get('players')
    if discard.get('actor')!=draw['player'] or not isinstance(players,(list,tuple)) or list(players)!=[draw['player']]:return False
    # Reuse the canonical typed private discard shape, with its fixed controller
    # placeholder restored after seat binding for representation validation.
    restored={**discard,'actor':'$controller','players':['$controller']}
    return bool(fixed_affected_player_discard_node_capabilities(effects=(restored,),target_schema=None,mechanic_ids=('fixed-affected-player-discard','cr-402-hand'),allow_controller=True))
