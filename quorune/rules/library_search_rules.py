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
        elif rule.mode=='top':limits.append(rule.top_count)
    return LibrarySearchConstraint(prohibited,min(limits) if limits else None)


def controlled_own_library_search(host, item, searcher, zones, *, library_owner=None):
    """Freeze the controlling source and all competing found-card effects."""
    if "library" not in zones or library_owner not in (None, searcher):
        return None
    from ..library_search_control import LibrarySearchControl, SearchControlSource
    from ..semantic_runtime.action_permissions import ActionPermissionKind, controller_action_permissions
    from ..semantic_runtime.library_search_rules import SEARCH_CONTROL_CAPABILITY

    if not any(SEARCH_CONTROL_CAPABILITY in program.capability_dependencies
               for program in host.semantics.programs_for_event("action.permission")):
        return None
    controls, replacements = {}, {}
    sources = {card.ref: card for card in host._semantic_event_sources(zones={"battlefield"})}
    for permission in controller_action_permissions(host, searcher):
        if permission.kind is not ActionPermissionKind.LIBRARY_SEARCH_RULE:
            continue
        mode = permission.library_search_rule.mode
        if mode not in {"control_opponent", "exile_opponent_found"}:
            continue
        program = host.semantics.get(permission.source_semantic_key)
        card = sources.get(permission.source_ref)
        if (program is None or not program_has_complete_card_program_admission(program)
                or SEARCH_CONTROL_CAPABILITY not in program.capability_dependencies or card is None
                or card.logical_object_id != permission.source_logical_object_id):
            continue
        current = SearchControlSource(card.ref, card.logical_object_id, card.controller, card.zone_timestamp)
        (controls if mode == "control_opponent" else replacements)[card.ref] = current
    if not controls and not replacements:
        return None
    order = lambda value: (value.timestamp, value.ref)
    controller = max(controls.values(), default=None, key=order)
    return LibrarySearchControl(item.ref, searcher, controller, tuple(sorted(replacements.values(), key=order)))


def library_search_object_ids(host,player,zone,*,searching_player=None):
    ids=tuple(host.state.players[player].zones[zone])
    if zone!='library':return ids
    searcher=player if searching_player is None else searching_player
    if type(searcher) is not str or not searcher or searcher not in host.state.players:
        raise ValueError('Library search requires a known searching player')
    constraint=library_search_constraint(host,searcher)
    if constraint.prohibited:return ()
    return ids[-constraint.top_count:] if constraint.top_count is not None else ids


def library_graveyard_search_choices(library_allowed):
    return ("library", "graveyard", "both") if library_allowed else ("graveyard",)


def public_graveyard_search_minimum(host, seat, zones, options):
    """A qualifying card in a public zone cannot be hidden by a failure to find."""
    refs = {row if isinstance(row, str) else row["id"] for row in options}
    return int("graveyard" in zones and any(
        host.state.cards[object_id].ref in refs for object_id in host.state.players[seat].zones["graveyard"]
    ))


def semantic_search_public_minimum(host, seat, effect, options):
    """Return the public-zone override for closed multi-zone search forms."""
    from ..library_search_model import CAST_X_ZONE_SEARCH_VERSION

    version = effect.get("schema_version")
    if version == 6:
        from .named_searches import named_zone_search_minimum

        retained = {key: value for key, value in effect.items() if key != "_search_completion"}
        return named_zone_search_minimum(host, seat, retained, options)
    if version == CAST_X_ZONE_SEARCH_VERSION:
        return public_graveyard_search_minimum(host, seat, effect["zone"], options)
    return None


def optional_search_permissions(host,actor,effect):
    from ..cast_x_search_model import CAST_X_ZONE_SEARCH_OPERATION, decode_cast_x_zone_search

    if effect.get("op") == CAST_X_ZONE_SEARCH_OPERATION:
        decode_cast_x_zone_search({key: effect[key] for key in ("op", "player", "search")}, actor=actor)
        return {actor: not library_search_constraint(host, actor).prohibited}
    if effect.get('op') == 'search_named_library_or_graveyard':
        from ..named_search_model import decode_named_zone_search
        decode_named_zone_search({key:effect[key] for key in ('op','player','search')}, actor=actor)
        return {actor:not library_search_constraint(host,actor).prohibited}
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
