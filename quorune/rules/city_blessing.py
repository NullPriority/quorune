from __future__ import annotations

"""Canonical acquisition of the persistent CR 702.131 player designation."""

from ..ability_fragments import StaticComponentSpec
from ..city_blessing_model import ASCEND_STATIC_HANDLER,CITYS_BLESSING_KEY,player_has_citys_blessing
from ..entry_copy import copiable_oracle_id


def controlled_permanent_count(state,player) -> int:
    return sum(card.zone=='battlefield' and card.controller==player and not card.phased_out
        for card in state.cards.values())


def award_citys_blessing(host,player,*,reason) -> bool:
    if player not in host.active_seats or player_has_citys_blessing(host.state.players[player]):return False
    if controlled_permanent_count(host.state,player)<10:return False
    host.state.players[player].stats[CITYS_BLESSING_KEY]=True
    host._log(player,'player.citys_blessing','Player gained the city\'s blessing.',
        {'player':player,'reason':reason},importance=2,changed_players=[player])
    return True


def synchronize_citys_blessing(host,*,reason='public state changed') -> bool:
    """Acquire from current trusted static components before trigger facts/SBAs."""
    semantics=getattr(host,'semantics',None)
    if semantics is None or not callable(getattr(host,'_effective_ability_fragments',None)):return False
    programs={p.key:p for p in semantics.programs() if p.active_zone=='battlefield'
        and any(d.get('handler_id')==ASCEND_STATIC_HANDLER for d in p.handlers)}
    if not programs:return False
    candidates={seat for seat in host.active_seats if not player_has_citys_blessing(host.state.players[seat])
        and controlled_permanent_count(host.state,seat)>=10}
    if not candidates:return False
    oracle_ids={p.oracle_id for p in programs.values()}
    holders=set()
    for card in host._semantic_event_sources(zones={'battlefield'}):
        if card.controller not in candidates or card.phased_out or copiable_oracle_id(card) not in oracle_ids:continue
        for fragment in host._effective_ability_fragments(card):
            if not isinstance(fragment,StaticComponentSpec):continue
            program=programs.get(fragment.semantic_key)
            if program is not None and host.semantic_program_is_current_trusted(program):
                holders.add(card.controller);break
    changed=False
    for seat in host.state.turn_order:
        if seat in holders:changed=award_citys_blessing(host,seat,reason=reason) or changed
    return changed
