"""Closed independent player instructions following source-self graveyard entry."""
from enum import StrEnum
from collections.abc import Mapping
from .source_self_entry_counter_model import CAPABILITY as ENTRY_COUNTER_CAPABILITY, MECHANIC as ENTRY_COUNTER_MECHANIC

FIELD = 'self_entry_followup'
VERSION = 1
MECHANIC = 'self-graveyard-entry-followup'
CAPABILITY = 'zone.self_move.fixed_player_followup'
FIXED_SELF_RETURN_MECHANIC = 'fixed-self-return-to-owner-hand'
SELF_ZONE_MOVE_MECHANIC_CAPABILITIES = {
    'self-zone-move': ('zone.self_move.activated',),
    'source-graveyard-hand-return': ('zone.return.triggered_self_graveyard_hand',),
    'source-graveyard-battlefield-return': ('zone.return.triggered_self_graveyard_battlefield',),
    MECHANIC: (CAPABILITY,),
    ENTRY_COUNTER_MECHANIC: (ENTRY_COUNTER_CAPABILITY,),
}
SELF_ZONE_MOVE_SHAPE_GATED_MECHANICS = (MECHANIC, ENTRY_COUNTER_MECHANIC)


class SelfEntryFollowup(StrEnum):
    LOSE_ONE_LIFE = 'lose_one_life'
    SKIP_NEXT_TURN = 'skip_next_turn'


def compile_self_entry_followup(text):
    return {
        'You lose 1 life.': SelfEntryFollowup.LOSE_ONE_LIFE,
        'You skip your next turn.': SelfEntryFollowup.SKIP_NEXT_TURN,
    }.get(text)


def self_entry_followup_effect(followup):
    followup = SelfEntryFollowup(followup)
    if followup is SelfEntryFollowup.LOSE_ONE_LIFE:
        return {'op': 'lose_life', 'player': '$controller', 'amount': 1}
    return {'op': 'schedule_turn_skip', 'player': '$controller',
            'spec': {'boundary': 'turn', 'scope': 'next', 'count': 1}}


def self_entry_followup_dependencies(followup):
    if SelfEntryFollowup(followup) is SelfEntryFollowup.LOSE_ONE_LIFE:
        return ('life.change.effect',)
    from .turn_skip_model import TURN_SKIP_CAPABILITY
    return (TURN_SKIP_CAPABILITY,)


def decode_self_entry_followup(effect):
    marker = effect.get(FIELD)
    if (not isinstance(marker, Mapping) or set(marker) != {'version', 'kind'}
            or type(marker['version']) is not int or marker['version'] != VERSION
            or type(marker['kind']) is not str):
        raise ValueError('Self-entry follow-up requires its explicit closed version')
    followup = SelfEntryFollowup(marker['kind'])
    fields = {'op', 'origin', 'destination', 'tapped', 'source_form', FIELD}
    if (set(effect) - {'_replacement_selections', '_aura_target_ref'} != fields
            or effect['op'] != 'self_zone_move' or effect['origin'] != 'graveyard'
            or effect['destination'] != 'battlefield' or effect['source_form'] != 'card'
            or type(effect['tapped']) is not bool):
        raise ValueError('Self-entry follow-up requires one closed graveyard entry')
    return followup


def self_entry_followup_payload_dependencies(value):
    if FIELD not in value:
        return ()
    followup = decode_self_entry_followup(value)
    return (CAPABILITY, 'zone.self_move.activated', *self_entry_followup_dependencies(followup))


def compiled_self_entry(spec):
    """Keep legacy instructions unchanged; append only the closed independent result."""
    effect = spec.effect()
    if spec.entry_counter is not None:
        return 'activated-self-zone-entry-counter-v3', (effect,), ('self-zone-move', ENTRY_COUNTER_MECHANIC)
    if spec.followup is None:
        return 'activated-self-zone-move-v1', (effect,), ('self-zone-move',)
    mechanic = ('cr-119-life' if spec.followup is SelfEntryFollowup.LOSE_ONE_LIFE
                else 'fixed-scheduled-turn-skip')
    return ('activated-self-zone-move-followup-v2',
            (effect, self_entry_followup_effect(spec.followup)),
            ('self-zone-move', MECHANIC, mechanic))
