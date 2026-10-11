from __future__ import annotations

"""Canonical player speed mutation and inherent once-per-turn trigger owner."""

from ..ability_fragments import StaticComponentSpec
from ..model import StackItem
from ..trigger_processing import enqueue_trigger_batch
from .player_speed_model import player_speed,SpeedIncrease,SPEED_KEY,SPEED_TRIGGER_TURN_KEY,START_ENGINES_HANDLER


def controlled_start_engines_players(host):
    players=set()
    if not callable(getattr(host,'_semantic_event_sources',None)):return ()
    for card in host._semantic_event_sources(zones={'battlefield'}):
        if card.controller not in host.active_seats or card.phased_out:continue
        for fragment in host._effective_ability_fragments(card):
            if not isinstance(fragment,StaticComponentSpec):continue
            program=host.semantics.get(fragment.semantic_key)
            if program is not None and host.semantic_program_is_current_trusted(program) and any(d.get('handler_id')==START_ENGINES_HANDLER for d in program.handlers):players.add(card.controller)
    return tuple(p for p in host.apnap_order() if p in players)


def start_player_speeds(host):
    players=tuple(p for p in controlled_start_engines_players(host) if player_speed(host.state.players[p]) is None)
    for seat in players:
        host.state.players[seat].stats[SPEED_KEY]=1
        host._log(seat,'player.speed','Player speed started.',{'player':seat,'before':None,'after':1},importance=1,changed_players=[seat])
    return bool(players)


def queue_speed_loss_trigger(host,transitions):
    seat=getattr(host.state,'active_player',None)
    if seat is None or seat not in getattr(host,'active_seats',()):return
    player=host.state.players[seat];speed=player_speed(player)
    if speed is None or not 1<=speed<4 or player.stats.get(SPEED_TRIGGER_TURN_KEY)==host.state.turn_sequence:return
    if not any(t.player!=seat and t.player in host.active_seats and t.after<t.before for t in transitions):return
    player.stats[SPEED_TRIGGER_TURN_KEY]=host.state.turn_sequence
    ref=host._next_ref('S')
    item=StackItem(stack_id=host._stable_runtime_id('stack',ref),ref=ref,kind='triggered_ability',controller=seat,label='Increase speed',
        semantic_key='builtin:player-speed',visibility=list(host.seats),context={'event':'player_lost_life','dynamic_effects':[{'op':'increase_speed','player':'$controller','amount':1,'if_less_than':4}]})
    enqueue_trigger_batch(host,(item,))


def commit_speed_increase(host,instruction:SpeedIncrease):
    if instruction.player not in host.active_seats:raise ValueError('Speed increase requires an active player')
    player=host.state.players[instruction.player];before=player_speed(player);amount=before if before is not None else 0
    if instruction.if_less_than is not None and amount>=instruction.if_less_than:return amount
    after=amount+instruction.amount
    player.stats[SPEED_KEY]=after
    host._log(instruction.player,'player.speed','Player speed increased.',{'player':instruction.player,'before':before,'after':after},importance=1,changed_players=[instruction.player])
    return after
