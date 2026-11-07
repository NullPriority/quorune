from __future__ import annotations

"""Materialize a selected card's public defender options without exposing the card."""

from ..replacement.immutable import FrozenMap
from ..rules.attacking_card_entry import AttackingEntryRecipient, validate_attacking_entry_choice
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation
from .token_destination_plans import legal_attacking_destinations


def prepare_attacking_card_entry_choice(effect, context):
    try:
        move = validate_attacking_entry_choice(effect, actor=context.actor)
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    status = context.query.attacking_card_entry_status
    if status is None:
        raise SemanticChoiceError("Attacking entry lacks its authoritative entry preview")
    if status == "missing":
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="the selected card is no longer available"))
    replacements = effect.get("_replacement_selections", ())

    def resolved_move(recipient):
        return FrozenMap({**move, "attacking_entry": {"schema_version": 1, "recipient": recipient},
            **({"_replacement_selections": replacements} if replacements else {})})

    destinations = legal_attacking_destinations(context.query, context.actor) if status == "attacking" else ()
    if not destinations:
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="entry is outside an available attack", prepend_effects=(resolved_move(None),)))
    options = []
    branches = {}
    for ref in destinations:
        card = context.query.object(ref, zones=("battlefield",)) if ref not in context.query.active_seats else None
        identity = card.logical_object_id if card is not None else None
        recipient = AttackingEntryRecipient(ref, identity)
        options.append({"id": ref, "label": card.printed_name if card is not None else ref})
        branches[ref] = [resolved_move(recipient.to_dict())]
    return {"op": "choose_option", "player": context.actor, "prompt": "Choose what the entering creature attacks.",
        "options": options, "then_by_choice": branches}
