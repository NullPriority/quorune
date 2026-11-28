"""Bind one existing direct-permanent target to a proved occurrence player."""

from dataclasses import replace
from . import regex_working_set as re

from .direct_target import DirectPermanentTargetSpec
from .event_player_results import _event_player
from ..rules.event_player_targets import (
    event_player_direct_target_schema, is_graveyard_type_opponent_upkeep_binding,
    is_source_combat_damage_player_binding,
)
from ..target_forms import EVENT_PLAYER_TARGET_REFERENCES


_ARTIFACT_AND_SOURCE_COUNTER = re.compile(
    r"(?P<destroy>destroy up to one target artifact you control) and "
    r"(?P<counter>put a \+1/\+1 counter on [^.]+)\.",
    re.IGNORECASE,
)


def _damage_linked_graveyard_partition(binding, body):
    if (body.casefold() != "exile up to that many target cards from their graveyard. "
            "put a +1/+1 counter on this creature for each creature card exiled this way. "
            "you gain 1 life for each noncreature card exiled this way."
            or not is_source_combat_damage_player_binding(binding.event.value, binding.event_condition)):
        return None
    from ..rules.targeted_graveyard_outcomes import damage_graveyard_outcome_template
    from .fixed_homogeneous_target_sets import FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC

    effect, schema = damage_graveyard_outcome_template()
    return ('damage-linked-graveyard-exile-outcome-v2', (effect,), schema,
            ('exile', 'cr-115-targets', FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC)), False


def event_player_target_result(binding, body, *, compile_result):
    if partition := _damage_linked_graveyard_partition(binding, body):
        return partition
    if (re.fullmatch(r"exile up to two target cards from that player['’]s graveyard\.", body, re.I)
            and is_source_combat_damage_player_binding(binding.event.value, binding.event_condition)):
        from ..rules.event_player_targets import damaged_player_graveyard_effect, damaged_player_graveyard_schema
        from .fixed_homogeneous_target_sets import FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC

        return ('damaged-player-graveyard-exile-v1', (damaged_player_graveyard_effect(),),
                damaged_player_graveyard_schema(), ('exile', 'cr-115-targets', FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC)), False
    matches = tuple(re.finditer(r'\b(?P<player>defending player|that player) controls\b', body, re.I))
    if len(matches) != 1:
        return None
    match = matches[0]
    subject = match['player'].casefold()
    scheduled_tap = (
        subject == 'that player' and body.casefold().removesuffix('.') == 'tap target creature that player controls'
        and is_graveyard_type_opponent_upkeep_binding(binding.event.value, binding.event_condition)
    )
    player = '$context.player' if scheduled_tap else _event_player(binding, subject)
    if player not in EVENT_PLAYER_TARGET_REFERENCES:
        return None
    normalized = body[:match.start()] + 'you control' + body[match.end():]
    sequence = _ARTIFACT_AND_SOURCE_COUNTER.fullmatch(normalized)
    if sequence is not None:
        if player != '$context.target' or not is_source_combat_damage_player_binding(
            binding.event.value, binding.event_condition,
        ):
            return None
        normalized = f"{sequence['destroy']}. {sequence['counter']}."
    result = compile_result(normalized)
    if result is None:
        return None
    compiled, source_result = result
    template, effects, schema, mechanics = compiled
    try:
        direct_schema = event_player_direct_target_schema(schema, effects)
        target = DirectPermanentTargetSpec.from_target_schema(direct_schema)
    except (TypeError, ValueError):
        return None
    if target.controller_relation != 'you' or target.controller_seat is not None:
        return None
    target = replace(target, controller_relation='any', controller_seat=player)
    target_schema = target.to_target_schema()
    if direct_schema is not schema:
        if sequence is None:
            return None
        del target_schema['count']
        target_schema['up_to'] = 1
    suffix = '-defending-player' if subject == 'defending player' else '-event-player'
    return (template + suffix, effects, target_schema, mechanics), source_result or scheduled_tap
