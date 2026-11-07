from __future__ import annotations

"""Seal damage-followup subjects and expose their existing zone replacement."""

from ..continuous_effect_model import ContinuousObjectIdentity
from ..continuous_effect_state import commit_continuous_effect
from ..damage_death_exile_model import DamageDeathExileScope, ResolvedDamageDeathExile
from ..errors import GameRuleError
from ..replacement_effects import ReplacementClass, ReplacementEffect, SetField


def damage_death_exile_subjects(scope, events, original_target=None):
    if scope is DamageDeathExileScope.TARGET:
        if original_target is None or original_target.kind != 'permanent' or 'creature' not in original_target.types:
            raise GameRuleError('Targeted death exile requires the original creature incarnation')
        return (ContinuousObjectIdentity(original_target.object_id, original_target.logical_object_id),)
    if scope is DamageDeathExileScope.DEALT_CREATURES:
        return tuple(dict.fromkeys(
            ContinuousObjectIdentity(event.target_object_id, event.target_logical_object_id)
            for event in events if event.was_dealt and event.target_kind == 'permanent' and 'creature' in event.target_types
        ))
    raise GameRuleError('Damage death exile recipient scope changed')


def commit_damage_death_exile(host, *, scope, source_ref, events, original_target=None):
    subjects = damage_death_exile_subjects(scope, events, original_target)
    if subjects:
        commit_continuous_effect(host.state, ResolvedDamageDeathExile(
            host._next_ref('DX'), source_ref, host._next_zone_timestamp(), subjects,
        ))


def damage_death_exile_replacements(state):
    for effect in state.continuous_effects or ():
        if isinstance(effect, ResolvedDamageDeathExile):
            for subject in effect.locked_objects:
                yield ReplacementEffect(effect_id=f'{effect.effect_id}:{subject.logical_object_id}',
                    source_id=effect.source_id, event_kind='zone.change', replacement_class=ReplacementClass.OTHER,
                    conditions={'origin': {'eq': 'battlefield'}, 'destination': {'eq': 'graveyard'},
                                'logical_object_id': {'eq': subject.logical_object_id}},
                    operations=(SetField('destination', 'exile'),), label='Exile this permanent instead of it dying')
