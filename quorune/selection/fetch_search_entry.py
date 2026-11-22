"""Seat-private replacement continuation for a committed fetch activation."""

from collections.abc import Mapping, Sequence

from ..errors import GameRuleError
from ..life_state import LifeStateError, prepare_life_payment
from ..replacement_effects import ReplacementChoiceRequired


FETCH_OPERATION_ID = "selection.search.fetch.v1"
_SEARCH_COMPLETION = "_search_completion"
_REPLACEMENTS = "_replacement_selections"
_CHOICE_FIELDS = ("search_card", "choice_made", "pay_life")
_ENTRY_FIELDS = {
    "operation_id", "actor", "stack_ref", "source_ref", "selected_ref",
    "selected_object_id", "selected_logical_id", "entry_pay_life",
}


def requires_private_land_entry_choice(required, seat):
    events = required.batch.events
    return (required.pending.choice.chooser == seat and len(events) == 1
            and events[0].kind == "zone.change"
            and "land" in events[0].payload.get("object_types", ()))


def finish_fetch_selection(host, item, *, selected, pay_life, replacement_selections=()):
    prior = {key: item.context[key] for key in _CHOICE_FIELDS if key in item.context}
    item.context.update(search_card=selected, choice_made=True, pay_life=pay_life)
    try:
        host._resolve_fetch_land(item, replacement_selections=replacement_selections)
    except ReplacementChoiceRequired as required:
        for key in _CHOICE_FIELDS:
            item.context.pop(key, None)
        item.context.update(prior)
        seat = item.controller
        if selected is None or not requires_private_land_entry_choice(required, seat):
            raise
        card = host._resolve_object(seat, selected, zones={"library"}, owned_only=True)
        if pay_life:
            record = host.card_record(card)
            if record is None:
                raise GameRuleError("Fetch search result has no card record")
            try:
                prepare_life_payment(host, seat, host._land_entry_life_amount(record))
            except LifeStateError as exc:
                raise GameRuleError(str(exc)) from exc
        from ..replacement_decisions import issue_replacement_order_choice

        result = {
            "op": "search",
            _SEARCH_COMPLETION: {
                "operation_id": FETCH_OPERATION_ID, "actor": seat,
                "stack_ref": item.ref, "source_ref": host._stack_source_ref(item),
                "selected_ref": selected, "selected_object_id": card.object_id,
                "selected_logical_id": card.logical_object_id, "entry_pay_life": pay_life,
            },
        }
        if replacement_selections:
            result[_REPLACEMENTS] = list(replacement_selections)
        issue_replacement_order_choice(
            host, item=item, effect=result, remaining=(), destination=None,
            note="Built-in fetchland search resolved", instruction_pointer=0, required=required,
        )


def is_fetch_search_entry_resume(effect):
    completion = effect.get(_SEARCH_COMPLETION)
    return isinstance(completion, Mapping) and completion.get("operation_id") == FETCH_OPERATION_ID


def resume_fetch_search_entry(host, item, effect, *, remaining, destination, instruction_pointer):
    completion = effect[_SEARCH_COMPLETION]
    selections = effect.get(_REPLACEMENTS)
    if (set(effect) != {"op", _SEARCH_COMPLETION, _REPLACEMENTS} or effect.get("op") != "search"
            or set(completion) != _ENTRY_FIELDS or item.context.get("builtin") != "fetch_land"
            or item.context.get("choice_made") or completion["actor"] != item.controller
            or completion["stack_ref"] != item.ref or completion["source_ref"] != host._stack_source_ref(item)
            or type(completion["entry_pay_life"]) is not bool or remaining or destination is not None
            or instruction_pointer != 0 or not isinstance(selections, Sequence)
            or isinstance(selections, (str, bytes)) or not selections):
        raise GameRuleError("Malformed fetch search entry continuation")
    reference = completion["selected_ref"]
    if type(reference) is not str or not reference:
        raise GameRuleError("Fetch search entry selection is malformed")
    options = {
        option["id"] for option in host._fetch_land_options(
            item.controller, item.context.get("land_types", []),
        )
    }
    if reference not in options:
        raise GameRuleError("Fetch search entry selection is no longer offered")
    card = host._resolve_object(item.controller, reference, zones={"library"}, owned_only=True)
    if (card.object_id != completion["selected_object_id"]
            or card.logical_object_id != completion["selected_logical_id"]):
        raise GameRuleError("Fetch search entry object changed")
    finish_fetch_selection(host, item, selected=reference, pay_life=completion["entry_pay_life"],
                           replacement_selections=selections)
