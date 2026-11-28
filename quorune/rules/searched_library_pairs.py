from __future__ import annotations

"""A private search for two differently named cards followed by an opponent's public choice."""

from collections.abc import Mapping
from dataclasses import replace

from ..carddb_characteristics import library_card_rules_names
from ..errors import GameRuleError
from ..library_order_model import SHUFFLE_CAPABILITY, fixed_shuffle_effect
from ..public_library_piles_model import (
    PILE_CAPABILITY, PILE_OPERATION, SEARCHED_PAIR_PILE_VERSION, PublicLibraryPileState, RevealedPileCard,
)
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects
from .library_partition import OrderedLibraryPartition
from .library_search_rules import library_search_object_ids
from .library_selection import LibrarySelectionObjectIdentity


SEARCHED_PAIR_VERSION = 77
SEARCHED_PAIR_CAPABILITY = "library.search.different_name_pair_opponent_choice"
SEARCHED_PAIR_CONTEXT = "revealed_searched_library_pair"
SEARCHED_PAIR_DESTINATION = "revealed_searched_pair"
SEARCHED_PAIR_STATE_FIELD = "_public_library_piles"
OPTIONAL_CHOICE_CAPABILITY = "effect.choice.optional_fixed"
SEARCHED_PAIR_CAPABILITIES = (
    SEARCHED_PAIR_CAPABILITY, OPTIONAL_CHOICE_CAPABILITY, PILE_CAPABILITY, SHUFFLE_CAPABILITY, "library.search.fixed_counted",
)
SEARCHED_PAIR_MECHANICS = ("fixed-optional-effect-choice",)


def _name(value):
    return " ".join(value.casefold().replace("’", "'").split())


def searched_pair_search(excluded_name):
    return {
        "op": "search", "schema_version": SEARCHED_PAIR_VERSION, "zone": "library",
        "selector": {"excluded_name": excluded_name},
        "count": {"minimum": 2, "maximum": 2},
        "destination": SEARCHED_PAIR_DESTINATION,
        "optional": True, "reveal": True, "shuffle_after": False,
        "instruction": "Find exactly two cards with different names, or find none.",
    }


def searched_pair_optional_effect(excluded_name):
    return {"op": "offer_optional_effect", "player": "$controller", "effects": [searched_pair_search(excluded_name)]}


def is_searched_pair_search(value):
    return isinstance(value, Mapping) and value.get("op") == "search" and value.get("schema_version") == SEARCHED_PAIR_VERSION


def validate_searched_pair_search(value):
    data = value.get("selector") if isinstance(value, Mapping) else None
    excluded = data.get("excluded_name") if isinstance(data, Mapping) else None
    if (
        type(excluded) is not str or not excluded or excluded != excluded.strip() or excluded.startswith("$")
        or type(value.get("schema_version")) is not int or value.get("optional") is not True
        or value.get("reveal") is not True or value.get("shuffle_after") is not False
        or FrozenMap(value) != FrozenMap(searched_pair_search(excluded))
    ):
        raise ValueError("A searched pair requires the exact two-card private search and deferred public result")
    return excluded


def searched_pair_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or not set(SEARCHED_PAIR_MECHANICS).issubset(mechanic_ids) or len(effects) != 1):
        return ()
    try:
        child, = effects[0]["effects"]
        excluded = validate_searched_pair_search(child)
        if FrozenMap(effects[0]) == FrozenMap(searched_pair_optional_effect(excluded)):
            return SEARCHED_PAIR_CAPABILITIES
    except (KeyError, TypeError, ValueError):
        pass
    return ()


def searched_pair_runtime_dependencies(program):
    if not (SEARCHED_PAIR_CAPABILITY in program.capability_dependencies
            or any(is_searched_pair_search(effect) for effect in nested_effects((program.effects, program.handlers)))):
        return ()
    required = searched_pair_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or program.handlers or program.cost_schema is not None or program.event_condition is not None
            or program.trigger_limit is not None):
        raise ValueError("A searched pair requires the original unconditional untargeted entry trigger")
    return required


def require_resolving_searched_pair(host, item, effect):
    try:
        expected = validate_searched_pair_search(effect)
        program = host.semantics.get(item.semantic_key)
        if (program is None or not host.semantic_program_is_current_trusted(program)
                or item.kind not in {"triggered_ability", "triggered_ability_copy"}
                or item.context.get("currently_resolving") is not True
                or not searched_pair_runtime_dependencies(program)
                or FrozenMap(program.effects[0]) != FrozenMap(searched_pair_optional_effect(expected))):
            raise ValueError("A searched pair requires its admitted resolving trigger")
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc


def searched_pair_options(host, actor, effect):
    excluded = _name(validate_searched_pair_search(effect))
    rows = []
    for object_id in library_search_object_ids(host, actor, "library"):
        card = host.state.cards[object_id]
        if card.object_kind != "card":
            continue
        record = host.card_record(card)
        names = {_name(name) for name in library_card_rules_names(record)} if record else set()
        if names and excluded not in names:
            rows.append(({"id": card.ref, "name": card.printed_name}, names))
    return [row for row, names in rows if any(names.isdisjoint(other_names) for _, other_names in rows)]


def _selected_pair(host, actor, refs, excluded):
    from ..selection.different_name_search import validate_search_different_names

    if len(refs) not in {0, 2}:
        raise GameRuleError("Find exactly two cards with different names, or find none")
    cards = tuple(host._resolve_object(actor, ref, zones={"library"}, owned_only=True) for ref in refs)
    if any(card.object_kind != "card" or (record := host.card_record(card)) is None
           or any(_name(name) == _name(excluded) for name in library_card_rules_names(record)) for card in cards):
        raise GameRuleError("The original two differently named searched cards changed")
    try:
        validate_search_different_names(host, {"different_names": True}, cards)
    except GameRuleError as exc:
        raise GameRuleError("Choose cards with different names") from exc
    return cards


def _singleton_piles(refs):
    first, second = refs
    return OrderedLibraryPartition(refs, (first,), (second,), "second")


def searched_pair_pile_state_is_closed(state):
    return (len(state.looked) == 2 and state.stage in {"choose_opponent", "choose_pile", "commit"}
            and state.groups == _singleton_piles(state.refs))


def start_searched_pair_piles(host, item, cards):
    from ..zone_transitions import ZoneTransitionOwner

    actor = item.controller
    looked = tuple(RevealedPileCard(LibrarySelectionObjectIdentity(
        card.object_id, card.logical_object_id, card.ref,
    ), card.printed_name) for card in cards)
    refs = tuple(card.identity.ref for card in looked)
    opponents = tuple(seat for seat in host.apnap_order() if seat != actor)
    ZoneTransitionOwner(host).reveal_library_search_results(
        actor, tuple(card.object_id for card in cards), public=True, announce_public_results=True,
    )
    if not opponents:
        return []
    opponent = opponents[0] if len(opponents) == 1 else None
    state = PublicLibraryPileState(
        actor, item.ref, item.label, looked, "choose_pile" if opponent else "choose_opponent",
        opponent=opponent, groups=_singleton_piles(refs),
    )
    item.context[SEARCHED_PAIR_CONTEXT] = [card.identity.to_dict() for card in looked]
    return [{
        "op": PILE_OPERATION, "schema_version": SEARCHED_PAIR_PILE_VERSION,
        "player": opponent or actor, "look_count": 2, "opponent_role": "chooser",
        SEARCHED_PAIR_STATE_FIELD: state.to_dict(),
    }]


def complete_searched_pair_search(host, context, choice_id, refs):
    excluded = validate_searched_pair_search(context.effect)
    cards = _selected_pair(host, context.seat, refs, excluded)
    followup = start_searched_pair_piles(host, context.item, cards) if cards else []
    remaining = [*followup, fixed_shuffle_effect(context.seat), *context.continuation.get("remaining", ())]
    publication = replace(context, effect={**context.effect, "reveal": bool(cards)},
                          continuation={**context.continuation, "remaining": remaining})
    host._record_semantic_search_result(
        choice_id, publication, destination_spec=SEARCHED_PAIR_DESTINATION, destination="library", moved=cards,
    )


def validate_searched_pair_placement(host, *, actor, player, stack_ref, arrangement, looked_are_public, selected_are_public):
    item = next((candidate for candidate in host.state.stack if candidate.ref == stack_ref), None)
    if (
        actor != player or not looked_are_public or not selected_are_public or item is None or item.controller != actor
        or [identity.to_dict() for identity in arrangement.looked] != item.context.get(SEARCHED_PAIR_CONTEXT)
    ):
        raise ValueError("A searched pair placement lost the exact revealed search receipt")
