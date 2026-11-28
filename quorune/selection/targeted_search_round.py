"""Collect and commit the private finds from one APNAP search round."""

from dataclasses import replace

from ..errors import GameRuleError
from ..rules.library_partition import place_private_library_find_on_top
from ..rules.library_search_rules import controlled_own_library_search, library_search_constraint
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..rules.targeted_search_round import (
    ROUND_FIELD, PrivateFind, TargetedSearchRound, one_private_library_search, target_pair_search_effect,
)
from ..zone_transitions import ZoneTransitionOwner
from .controlled_search import apply_controlled_finds, resolve_controlled_find_controllers


def _participants(host, item):
    targets = tuple(target for target in item.targets if target is not None)
    order = tuple(seat for seat in host.apnap_order() if seat in targets)
    program = host.semantics.get(item.semantic_key)
    if (item.context.get("currently_resolving") is not True
            or item.context.get("targets_revalidated") is not True
            or len(item.targets) != 2 or not 1 <= len(targets) <= 2 or len(order) != len(targets)
            or program is None or tuple(dict(effect) for effect in program.effects) != (target_pair_search_effect(),)
            or not host.semantic_program_is_current_trusted(program)):
        raise GameRuleError("The private search round lost its original resolving pair")
    return order


def begin_round(host, item, effect, *, remaining, destination, note, instruction_pointer):
    participants = _participants(host, item)
    targets = [target for target in item.targets if target is not None]
    if type(effect.get(ROUND_FIELD)) is not int or effect != {**target_pair_search_effect(), "targeted_players": targets}:
        raise GameRuleError("The private search round requires its revalidated player targets")
    _advance_round(host, item, TargetedSearchRound(participants), remaining, destination, note, instruction_pointer)


def _advance_round(host, item, round_state, remaining, destination, note, instruction_pointer):
    while (seat := round_state.next_seat) is not None:
        if not library_search_constraint(host, seat).prohibited:
            host._issue_semantic_search(
                item=item, seat=seat, effect=one_private_library_search(), remaining=remaining,
                destination=destination, note=note, instruction_pointer=instruction_pointer,
                extra_continuation={ROUND_FIELD: round_state.to_dict()},
            )
            return
        round_state = replace(round_state, findings=(*round_state.findings, PrivateFind(seat, None, None)))
    _commit_round(host, item, round_state)
    host._continue_resolution(
        stack_ref=item.ref, effects=list(remaining), destination=destination, note=note,
        instruction_pointer=instruction_pointer + 1,
    )


def validate_round_choice(host, item, decision, seat, effect):
    if ROUND_FIELD not in decision.continuation:
        return None
    state = TargetedSearchRound.from_dict(decision.continuation[ROUND_FIELD])
    if (state.seats != _participants(host, item) or state.next_seat != seat
            or library_search_constraint(host, seat).prohibited or effect != one_private_library_search()):
        raise GameRuleError("The private search decision changed its original actor or instruction")
    return state


def complete_round_choice(host, decision, context, values, zones):
    if (set(context.response) - {"action", "choice_schema", "search_cards"}
            or context.response.get("search_cards") is None):
        raise GameRuleError("Choose the exact offered private search card or the empty selection")
    round_state = validate_round_choice(host, context.item, decision, context.seat, context.effect)
    winners = resolve_controlled_find_controllers(
        host, decision, item=context.item, actor=context.seat, zones=zones, refs=values,
    )
    if winners is None:
        return
    identity = None
    if values:
        card = host._resolve_object(context.seat, values[0], zones={"library"}, owned_only=True)
        identity = LibrarySelectionObjectIdentity(card.object_id, card.logical_object_id, card.ref)
    find = PrivateFind(context.seat, decision.decision_id, identity, winners[0] if winners else None)
    _advance_round(
        host, context.item, replace(round_state, findings=(*round_state.findings, find)),
        context.continuation["remaining"], context.continuation["destination"],
        context.continuation["note"], context.frame["instruction_pointer"],
    )


def _current_finds(host, item, results):
    found = {}
    for result in results:
        seat = result.seat
        prohibited = library_search_constraint(host, seat).prohibited
        if prohibited != (result.choice_id is None):
            raise GameRuleError("The private search round changed a player's opportunity to search")
        if prohibited:
            continue
        refs = {entry["id"] for entry in host._semantic_search_options(seat, one_private_library_search())}
        if (result.card is None) != (not refs):
            raise GameRuleError("The private search round lost its mandatory find")
        if result.card is None:
            continue
        original = result.card
        card = host.state.cards.get(original.object_id)
        if (card is None or card.owner != seat or card.zone != "library"
                or (card.logical_object_id, card.ref) != (original.logical_object_id, original.ref)
                or card.ref not in refs):
            raise GameRuleError("An original private library find no longer matches")
        control = controlled_own_library_search(host, item, seat, ("library",))
        controllers = {source.controller for source in control.replacements} if control else set()
        if ((result.destination_controller is None) != (not controllers)
                or (controllers and result.destination_controller not in controllers)):
            raise GameRuleError("The private search round lost its exact find replacement")
        found[seat] = card
    return found


def _commit_round(host, item, round_state):
    results = round_state.findings
    found = _current_finds(host, item, results)
    ordinary = dict(found)
    owner = ZoneTransitionOwner(host)
    reason = f"{item.label} resolved"
    for result in results:
        seat = result.seat
        if result.choice_id is None:
            host._log(seat, "library.search.prohibited", f"{seat} could not search their library.",
                      {"source": item.ref}, importance=1)
            continue
        if result.destination_controller is not None:
            apply_controlled_finds(host, actor=seat, zones=("library",),
                                   refs=(found[seat].ref,), controllers=(result.destination_controller,))
            ordinary.pop(seat)
        host._log_semantic_search_result(
            seat, item, one_private_library_search(), destination_spec="library_top", destination="library",
            moved=(() if seat not in ordinary else (ordinary[seat],)),
        )
    for seat in round_state.seats:
        card = ordinary.get(seat)
        owner.shuffle_library(seat, reason=reason, excluded_object_ids=(card.object_id,) if card else ())
    for seat, card in ordinary.items():
        place_private_library_find_on_top(host.state, seat, card.object_id)
