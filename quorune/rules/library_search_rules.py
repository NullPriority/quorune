from __future__ import annotations

"""Read-only search constraints and canonical commitment of a resolved prohibition."""

from dataclasses import dataclass
from ..library_search_model import ResolvedLibrarySearchProhibition
from ..card_programs.admission import program_has_complete_card_program_admission
from ..continuous_effect_state import commit_continuous_effect


@dataclass(frozen=True,slots=True)
class LibrarySearchConstraint:
    prohibited:bool=False
    top_count:int|None=None


def library_search_constraint(host,player):
    from ..semantic_runtime.action_permissions import ActionPermissionKind,controller_action_permissions
    prohibited=any(isinstance(effect,ResolvedLibrarySearchProhibition) for effect in host.state.continuous_effects or ())
    limits=[]
    for permission in controller_action_permissions(host,player):
        if permission.kind is not ActionPermissionKind.LIBRARY_SEARCH_RULE:continue
        program=host.semantics.get(permission.source_semantic_key)
        if program is None or not program_has_complete_card_program_admission(program):continue
        rule=permission.library_search_rule
        if rule.mode=='prohibit':prohibited=True
        else:limits.append(rule.top_count)
    return LibrarySearchConstraint(prohibited,min(limits) if limits else None)


def library_search_object_ids(host,player,zone):
    ids=tuple(host.state.players[player].zones[zone])
    if zone!='library':return ids
    constraint=library_search_constraint(host,player)
    if constraint.prohibited:return ()
    return ids[-constraint.top_count:] if constraint.top_count is not None else ids


def optional_search_permissions(host,actor,effect):
    if effect.get('op')!='offer_optional_effect':return {}
    effects=effect.get('effects',())
    if len(effects)!=1 or effects[0].get('op')!='search':return {}
    zone=effects[0].get('zone','library')
    if zone!='library' and zone!=['library'] and zone!=('library',):return {}
    player=effects[0].get('searching_player') or actor
    return {player:not library_search_constraint(host,player).prohibited}


def apply_library_search_prohibition(host,*,actor,source_id):
    host._require_seat(actor,in_game=True)
    effect=ResolvedLibrarySearchProhibition(host._next_ref('LS'),source_id,host._next_zone_timestamp(),actor)
    commit_continuous_effect(host.state,effect)
    host._log(actor,'library.search.restricted','Players cannot search libraries this turn.',{'effect_id':effect.effect_id},importance=1)
    return effect.effect_id
