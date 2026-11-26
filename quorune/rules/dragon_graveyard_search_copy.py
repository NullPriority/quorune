"""The exact library choice and public model of a temporary self-copy."""

from collections.abc import Mapping

from ..defined_token_faces import single_face_copy_values
from ..entry_results import record_temporary_copy
from ..errors import GameRuleError
from ..library_search_model import DRAGON_GRAVEYARD_COPY_SEARCH_VERSION
from ..replacement.immutable import FrozenMap, thaw_value
from ..replacement.face_status_copy import COPY_HISTORY_FIELD
from .library_selection import LibrarySelectionObjectIdentity


DRAGON_GRAVEYARD_COPY_SEARCH_CAPABILITY = "library.search.dragon_graveyard_self_copy"
DRAGON_GRAVEYARD_COPY_SEARCH_MECHANIC = "dragon-graveyard-search-and-self-copy"
_CANDIDATE_FIELD = "_dragon_graveyard_copy_candidates"
_RESUME_FIELDS = {"_search_completion", "_replacement_selections"}
_PERMANENT_TYPES = ["artifact", "battle", "creature", "enchantment", "land", "planeswalker"]
_PUBLIC_ZONES = {"battlefield", "command", "exile", "graveyard", "stack"}
_REQUIRED = (
    DRAGON_GRAVEYARD_COPY_SEARCH_CAPABILITY, "library.search.fixed_counted", "library.search.restriction.static",
    "zone.change.destination_replacement", "continuous.resolution.temporary_self_copy",
)


def dragon_graveyard_copy_search_effect(player="$controller"):
    return {
        "op": "search", "schema_version": DRAGON_GRAVEYARD_COPY_SEARCH_VERSION, "searching_player": player,
        "zone": "library", "selector": {"types_any": list(_PERMANENT_TYPES), "subtypes_any": ["dragon"]},
        "count": {"minimum": 1, "maximum": 1}, "optional": False, "destination": "graveyard",
        "reveal": False, "shuffle_after": True,
    }


def is_dragon_graveyard_copy_search(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "search"
            and effect.get("schema_version") == DRAGON_GRAVEYARD_COPY_SEARCH_VERSION)


def validate_dragon_graveyard_copy_search(effect, *, player="$controller", bound=False):
    if not is_dragon_graveyard_copy_search(effect) or type(effect["schema_version"]) is not int:
        raise ValueError("A Dragon copy search requires its exact search version")
    base = {key: value for key, value in effect.items()
            if not bound or key not in _RESUME_FIELDS | {_CANDIDATE_FIELD}}
    count = base.get("count")
    if (type(player) is not str or not player or (bound and player.startswith("$"))
            or not isinstance(count, Mapping) or any(type(count.get(key)) is not int for key in ("minimum", "maximum"))
            or any(type(base.get(key)) is not bool for key in ("optional", "reveal", "shuffle_after"))
            or FrozenMap(base) != FrozenMap(dragon_graveyard_copy_search_effect(player))):
        raise ValueError("The Dragon search requires its own library and exact graveyard and shuffle instruction")
    if not bound or _CANDIDATE_FIELD not in effect:
        return ()
    raw = effect[_CANDIDATE_FIELD]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("The Dragon search candidate identities must be ordered")
    candidates = tuple(LibrarySelectionObjectIdentity.from_dict(row) for row in raw)
    if len(candidates) != len({candidate.object_id for candidate in candidates}):
        raise ValueError("The Dragon search candidate identities must be distinct")
    return candidates


def dragon_graveyard_copy_search_payload_dependencies(effect):
    if not is_dragon_graveyard_copy_search(effect):
        return ()
    validate_dragon_graveyard_copy_search(effect)
    return _REQUIRED


def dragon_graveyard_copy_search_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or target_schema is not None or DRAGON_GRAVEYARD_COPY_SEARCH_MECHANIC not in mechanic_ids:
        return ()
    try:
        return dragon_graveyard_copy_search_payload_dependencies(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()


def dragon_graveyard_copy_search_runtime_dependencies(program):
    if not any(is_dragon_graveyard_copy_search(effect) for effect in program.effects):
        return ()
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

    owners = dragon_graveyard_copy_search_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=(DRAGON_GRAVEYARD_COPY_SEARCH_MECHANIC,),
    )
    if (not owners or (program.event, program.active_zone) != ("activate", "battlefield")
            or len(program.handlers) != 1 or program.event_condition is not None
            or (program.cost_schema or {}).get("text") != "{2}"):
        raise ValueError("The Dragon search requires its complete ordinary battlefield activation")
    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    if (not ability.compiled_cost or ability.mana_ability or ability.builtin_semantic_key != program.key
            or ability.cost_text != "{2}" or ability.target_schema is not None):
        raise ValueError("The Dragon search changed its original activation catalog")
    return owners


def require_dragon_graveyard_copy_search(host, item, effect, actor):
    validate_dragon_graveyard_copy_search(effect, player=actor, bound=True)
    program = host.semantics.get(item.semantic_key)
    if (actor != item.controller or item.kind != "activated_ability" or item.targets
            or item.context.get("currently_resolving") is not True or program is None
            or DRAGON_GRAVEYARD_COPY_SEARCH_CAPABILITY not in program.capability_dependencies
            or not host.semantic_program_is_current_trusted(program)
            or not dragon_graveyard_copy_search_runtime_dependencies(program)):
        raise GameRuleError("The Dragon search requires its original resolving activation")


def bind_dragon_graveyard_copy_search(host, item, effect, actor):
    from .library_search_rules import library_search_object_ids

    if _CANDIDATE_FIELD in effect or _RESUME_FIELDS & set(effect):
        raise GameRuleError("A fresh Dragon search cannot carry saved candidates or results")
    require_dragon_graveyard_copy_search(host, item, effect, actor)
    cards = (host.state.cards[oid] for oid in library_search_object_ids(host, actor, "library", searching_player=actor))
    candidates = tuple(LibrarySelectionObjectIdentity(card.object_id, card.logical_object_id, card.ref).to_dict()
                       for card in cards if host._search_candidate_matches(card, effect["selector"]))
    return {**effect, _CANDIDATE_FIELD: candidates}


def move_dragon_graveyard_copy_search_card(host, context, values, replacement_selections):
    from ..zone_transitions import ZoneTransitionOwner

    effect, actor = context.effect, context.seat
    require_dragon_graveyard_copy_search(host, context.item, effect, actor)
    candidates = validate_dragon_graveyard_copy_search(effect, player=actor, bound=True)
    if _CANDIDATE_FIELD not in effect or len(values) > 1:
        raise GameRuleError("The Dragon search requires its pinned private window and at most one result")
    if not values:
        return []
    matches = [candidate for candidate in candidates if candidate.ref == values[0]]
    if len(matches) != 1:
        raise GameRuleError("The selected Dragon was not in the original search window")
    identity, = matches
    card = host.state.cards.get(identity.object_id)
    if (card is None or card.logical_object_id != identity.logical_object_id or card.ref != identity.ref
            or card.owner != actor or card.zone != "library" or not card.is_card_object
            or not host._search_candidate_matches(card, effect["selector"])):
        raise GameRuleError("The selected Dragon no longer has its original library incarnation")
    moved = ZoneTransitionOwner(host).move_card(
        card.object_id, "graveyard", reason=f"{context.item.label} search", log=False,
        semantic_events=True, replacement_selections=replacement_selections,
    )
    return [moved]


def copy_public_dragon_search_result(host, context, moved):
    if not moved:
        return
    require_dragon_graveyard_copy_search(host, context.item, context.effect, context.seat)
    model, = moved
    if model.zone not in _PUBLIC_ZONES or model.face_down or not model.is_card_object:
        return
    item = context.item
    source = host.state.cards.get(item.source_object_id)
    if (source is None or source.zone != "battlefield" or source.phased_out
            or source.logical_object_id != item.context.get("source_logical_object_id")):
        return
    values = single_face_copy_values(host._copyable_characteristics(model))
    timestamp = host._next_zone_timestamp() if COPY_HISTORY_FIELD in source.annotations else 0
    record_temporary_copy(source, values, timestamp=timestamp)
    host._log(context.seat, "permanent.copy", f"{source.ref} became a copy of {model.ref} until end of turn.",
              {"source": source.ref, "target": model.ref, "duration": "until_end_of_turn", "reason": item.label},
              importance=2, changed_objects=[source.object_id])
