"""Resume the simultaneous reveal entry after its native Aura or Battle choice."""

from collections.abc import Mapping
from dataclasses import asdict

from ..replacement.immutable import FrozenMap
from ..rules.announced_permanent_reveal import OPERATION
from ..rules.batched_entry_choices import BatchEntryChoice
from .intent_replacement import serialized_replacement_selections
from .model import (
    ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest,
)

ENTRY_INPUTS = "revealed_entry_inputs"
PENDING_ENTRY_CHOICE = "revealed_pending_entry_choice"


def revealed_entry_inputs(effect, selected):
    raw = effect.get(ENTRY_INPUTS, {"aura_targets": {}, "battle_protectors": {}, "replacement_selections": ()})
    if (not isinstance(raw, Mapping) or set(raw) != {"aura_targets", "battle_protectors", "replacement_selections"}
            or not isinstance(raw["replacement_selections"], (tuple, list)) or PENDING_ENTRY_CHOICE in effect):
        raise SemanticChoiceError("The revealed entry has an incomplete choice continuation")
    for field in ("aura_targets", "battle_protectors"):
        values = raw[field]
        if (not isinstance(values, Mapping) or set(values) - set(selected)
                or any(type(value) is not str or not value for value in values.values())):
            raise SemanticChoiceError("The selected entry choices must belong to the original batch")
    return raw


def prepare_revealed_entry_choice(host, continuation, intent, required, *, index, count):
    choice = required.choice
    if (index != 0 or count != 1 or choice.actor != intent.actor or choice.object_ref not in intent.object_refs
            or continuation.semantic_frame.controller != intent.actor):
        raise SemanticChoiceError("A simultaneous entry choice must belong to the original caster and selected batch")
    card = host.state.cards.get(choice.object_id)
    if card is None or card.ref != choice.object_ref or card.logical_object_id != choice.logical_object_id or card.zone != "library":
        raise SemanticChoiceError("The selected revealed entrant changed before the choice")
    raw = {
        "aura_targets": intent.aura_targets, "battle_protectors": intent.battle_protectors,
        "replacement_selections": serialized_replacement_selections(intent.replacement_selections),
    }
    request = SemanticChoiceRequest(
        "Choose what this revealed Aura will enchant." if choice.kind == "attachment"
        else "Choose the opponent who will protect this revealed Siege.",
        ScalarChoice(field_name=choice.kind, legal_values=choice.options),
        FrozenMap({"stack": continuation.stack_ref, "operation": OPERATION, "entering": card.ref,
                   "objects": ({"id": card.ref, "name": card.printed_name},)}),
    )
    saved = FrozenMap({**continuation.effect, ENTRY_INPUTS: raw, PENDING_ENTRY_CHOICE: asdict(choice)})
    return choice.actor, SemanticChoicePreparation(request, saved)


def complete_revealed_entry_choice(continuation, response, query, selected):
    effect = dict(continuation.effect)
    raw = effect.pop(PENDING_ENTRY_CHOICE)
    if not isinstance(raw, Mapping) or set(raw) != {"kind", "actor", "object_ref", "object_id", "logical_object_id", "options"}:
        raise SemanticChoiceError("The revealed entrant did not retain its entry choice")
    choice = BatchEntryChoice(**raw)
    picked = response.get(choice.kind)
    row = query.object(choice.object_ref, zones=("library",))
    if (choice.actor != continuation.semantic_frame.controller or choice.object_ref not in selected
            or row is None or row.object_id != choice.object_id or row.logical_object_id != choice.logical_object_id
            or set(response) - {"action", "action_id", "choice_schema", choice.kind}
            or type(picked) is not str or picked not in choice.options):
        raise SemanticChoiceError("Choose a legal current attachment or protector for the original revealed card")
    retained = revealed_entry_inputs(effect, selected)
    field = "aura_targets" if choice.kind == "attachment" else "battle_protectors"
    if choice.object_ref in retained[field]:
        raise SemanticChoiceError("An entry choice cannot be answered twice")
    effect[ENTRY_INPUTS] = {**retained, field: {**retained[field], choice.object_ref: picked}}
    return SemanticChoiceCompletion(prepend_effects=(FrozenMap(effect),))



def revealed_battlefield_entry_options(host, cards, intent):
    # Direct nonspell battlefield entry chooses the front of a double-faced card.
    object_ids = {card.ref: card.object_id for card in cards}
    faces = {}
    for card in cards:
        record = host.card_record(card)
        if record is not None and record.layout in ("modal_dfc", "transform") and record.faces:
            name = record.faces[0].get("name")
            if type(name) is not str or not name:
                raise SemanticChoiceError("A selected double-faced card is missing its original front")
            faces[card.object_id] = name
    return {
        "aura_target_refs": {object_ids[ref]: target for ref, target in intent.aura_targets.items()},
        "battle_protectors": {object_ids[ref]: seat for ref, seat in intent.battle_protectors.items()},
        "entry_faces": faces,
    }
