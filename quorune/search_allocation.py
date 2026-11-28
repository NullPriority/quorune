"""Execute one sealed ordered search assignment through the shared zone owner."""
from .errors import GameRuleError
from .public_library_piles_model import (
    DISTINCT_PUBLIC_SEARCH_SPLIT_VERSION, DISTINCT_SEARCHED_PILE_VERSION,
    PUBLIC_SEARCH_SPLIT_CAPTURE, SEARCHED_PILE_VERSION, RevealedPileCard, SearchedLibraryPileState,
    searched_pile_effect, validate_public_search_split,
)
from .rules.library_selection import LibrarySelectionObjectIdentity
from .rules.library_result_capability_shapes import public_search_split_runtime_dependencies
from .search_allocation_model import SearchAllocationSpec
from .selection.different_name_search import require_distinct_search_card_names
from .zone_transitions import ZoneTransitionOwner


def move_allocated_search_results(host, context, selected_cards, replacement_selections):
    spec = SearchAllocationSpec.from_effect(context.effect)
    owner = ZoneTransitionOwner(host);seat = context.seat
    object_ids = tuple(card.object_id for card in selected_cards)
    changes = tuple(zip(object_ids, spec.destinations(selected_cards), strict=True))
    owner.reveal_library_search_results(seat, object_ids, public=spec.search.reveal)
    entrants = {oid: seat for oid, dest in changes if dest == 'battlefield'}
    moved = owner.move_cards_simultaneously(changes, reason=f'{context.item.label} search', log=False,
        destination_controllers=entrants, putting_players=entrants,
        tapped_by_object={oid: spec.first_tapped for oid, dest in changes if dest == 'battlefield'},
        replacement_selections=replacement_selections)
    if spec.search.reveal:
        owner.reveal_library_search_results(seat, tuple(c.object_id for c in moved if c.zone == 'hand'), public=True, expected_zone='hand')
    return moved


def require_public_search_split_resolution(host, item, effect):
    chooser = validate_public_search_split(effect, resolved=True)
    program = host.semantics.get(item.semantic_key)
    if (item.kind not in {"spell", "spell_copy"} or item.context.get("currently_resolving") is not True
            or item.context.get("targets_revalidated") is not True or tuple(item.targets) != (chooser,)
            or chooser == item.controller or program is None or not host.semantic_program_is_current_trusted(program)):
        raise GameRuleError("The private search requires its exact resolving spell and opponent")
    dependencies = public_search_split_runtime_dependencies(program)
    if not dependencies or not set(dependencies) <= set(program.capability_dependencies):
        raise GameRuleError("The public partition has no complete trusted search")
    return chooser


def stage_public_search_split(host, context, values, *, choice_id):
    item, seat = context.item, context.seat
    chooser = require_public_search_split_resolution(host, item, context.effect)
    if seat != item.controller or PUBLIC_SEARCH_SPLIT_CAPTURE in item.context:
        raise GameRuleError("The public partition lost its original private search")
    cards = tuple(host._resolve_object(seat, ref, zones={"library"}, owned_only=True) for ref in values)
    distinct = context.effect["schema_version"] == DISTINCT_PUBLIC_SEARCH_SPLIT_VERSION
    limit = min(4 if distinct else 3, len(context.options))
    valid_count = len(cards) <= limit if distinct else len(cards) == limit
    if not valid_count or len({card.object_id for card in cards}) != len(cards):
        raise GameRuleError("The public partition requires the complete private search result")
    if distinct:
        require_distinct_search_card_names(host, cards)
    remaining = list(context.continuation.get("remaining", ()))
    if cards:
        looked = tuple(
            RevealedPileCard(LibrarySelectionObjectIdentity(card.object_id, card.logical_object_id, card.ref),
                             card.printed_name)
            for card in cards
        )
        active_chooser = host.state.players[chooser].in_game
        stage = "choose_card" if active_chooser else "choose_opponent"
        current_chooser = chooser if active_chooser else None
        chosen = None
        if distinct and len(cards) <= 2:
            stage, current_chooser = "commit", chooser
            chosen = tuple(card.ref for card in cards)
        state = SearchedLibraryPileState(
            seat, item.ref, item.label, chooser, looked,
            stage, current_chooser, chosen=chosen,
            schema_version=DISTINCT_SEARCHED_PILE_VERSION if distinct else SEARCHED_PILE_VERSION,
        )
        item.context[PUBLIC_SEARCH_SPLIT_CAPTURE] = state.to_dict()
        ZoneTransitionOwner(host).reveal_library_search_results(
            seat, tuple(card.object_id for card in cards), public=True, announce_public_results=True,
        )
        remaining.insert(0, searched_pile_effect(state))
    host._log(seat, "library.search", f"{seat} searched their library and found {len(cards)} card(s).",
              {"source": item.ref, "destination": "allocated", "count": len(cards),
               "revealed": True, "objects": list(values)}, importance=2,
              changed_objects=[card.object_id for card in cards], changed_players=[seat])
    item.context.setdefault("semantic_continuations", []).append(
        {**context.frame, "pending_choice_id": choice_id, "choice_result": list(values), "resumed": True},
    )
    if not cards:
        host.shuffle_library(seat, reason=f"{item.label} resolved")
    host._continue_resolution(
        stack_ref=context.stack_ref, effects=remaining, destination=context.continuation.get("destination"),
        note=str(context.continuation.get("note") or ""),
        instruction_pointer=int(context.frame["instruction_pointer"]) + 1,
    )


def allocated_search_result_details(effect, moved):
    if effect.get('schema_version') != 5:return {}
    SearchAllocationSpec.from_effect(effect)
    return {'destinations': {card.ref: card.zone for card in moved}}
