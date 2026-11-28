"""Batch preflight retains selected Aura attachments and Siege protectors."""

from dataclasses import dataclass

from ..aura import preflight_aura_zone_move
from ..aura.model import AuraEntryChoiceRequired
from ..entry_copy import copied_entry_preview
from ..entry_counters import prospective_battle_entry_protector
from ..errors import GameRuleError
from .room_copy_values import room_copy_characteristics
from ..entry_counters import capture_prospective_entry_characteristics


@dataclass(frozen=True, slots=True)
class BatchEntryChoice:
    kind: str
    actor: str
    object_ref: str
    object_id: str
    logical_object_id: str
    options: tuple[str, ...]

    def __post_init__(self):
        if (self.kind not in {"attachment", "protector"}
                or any(type(value) is not str or not value for value in
                       (self.actor, self.object_ref, self.object_id, self.logical_object_id))
                or not isinstance(self.options, tuple) or not self.options
                or any(type(value) is not str or not value for value in self.options)
                or len(set(self.options)) != len(self.options)):
            raise ValueError("A batch entry choice must bind one live entrant and its legal options")


class BatchEntryChoiceRequired(GameRuleError):
    def __init__(self, choice):
        self.choice = choice
        super().__init__("The simultaneous arrival requires a legal entry choice")


def _entry_choice(kind, actor, card, options):
    return BatchEntryChoice(kind, actor, card.ref, card.object_id, card.logical_object_id, tuple(options))


def _entry_preview(host, card, replacement, data):
    copied = copied_entry_preview(card, replacement)
    if copied is None:
        return card, data, None
    return copied, room_copy_characteristics(copied, replacement.event.payload["entry_copy"]), host._compiled_enchant_spec(copied)


def preflight_batch_auras(host, changes, prepared, characteristics, controllers, targets, log, *, capture_choices):
    admitted = []
    for object_id, destination in changes:
        card = host.state.cards[object_id]
        replacement = prepared[object_id]
        data = characteristics.get(object_id, {})
        subject, data, spec = _entry_preview(host, card, replacement, data) if capture_choices else (card, data, None)
        try:
            plan = preflight_aura_zone_move(
                host, subject, destination=replacement.destination, requested_destination=destination,
                destination_type_line=str(data.get("type_line") or ""), enter_face=None,
                enchant_spec=spec, controller=controllers.get(object_id),
                target_ref=targets.get(object_id), resolving_as_spell=False,
                origin=card.zone, log=log, error_type=GameRuleError,
            )
        except AuraEntryChoiceRequired as required:
            if not capture_choices:
                raise
            raise BatchEntryChoiceRequired(_entry_choice(
                "attachment", required.plan.controller, card, required.plan.legal_target_refs,
            )) from required
        if capture_choices and targets.get(object_id) is not None and plan.remain_in_origin:
            raise GameRuleError("The selected Aura attachment changed before simultaneous entry")
        if not plan.remain_in_origin:
            admitted.append((object_id, destination))
    return tuple(admitted)


def preflight_batch_protectors(host, changes, prepared, controllers, faces, protectors):
    for object_id, _ in changes:
        result = prepared[object_id]
        if result.destination != "battlefield":
            continue
        card = host.state.cards[object_id]
        data, _ = capture_prospective_entry_characteristics(host, card=card, enter_face=faces.get(object_id))
        _, data, _ = _entry_preview(host, card, result, data)
        types, subtypes, _ = host._type_parts(str(data.get("type_line") or ""))
        if "battle" not in types:
            continue
        controller = controllers.get(object_id) or card.owner
        selected = protectors.get(object_id)
        options = tuple(seat for seat in host.active_seats if seat != controller)
        if "siege" in subtypes and selected is None and options:
            raise BatchEntryChoiceRequired(_entry_choice("protector", controller, card, options))
        prospective_battle_entry_protector(
            destination=result.destination, entry_characteristics=data, controller=controller,
            supplied_protector=selected, active_seats=host.active_seats, error_type=GameRuleError,
        )
