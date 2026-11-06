from __future__ import annotations

"""Keep a copied Aura's entry choices separate from the copy effect's targets."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from .aura.model import AuraEntryChoiceRequired, AuraEntryOutcome, AuraRuleError
from .aura.runtime import prepare_aura_entry
from .continuous_effect_model import ContinuousObjectIdentity
from .token_copy_recipes import TokenCopyRecipeSpec


COPIED_AURA_TOKEN_CHOICE = "copied_aura_token_choice"
COPIED_AURA_TOKEN_SELECTIONS = "copied_aura_token_selections"


def _copy_slot(value: Any) -> tuple[int, int]:
    if not isinstance(value, (tuple, list)) or len(value) != 2 or any(
        type(index) is not int or index < 0 for index in value
    ):
        raise AuraRuleError("Copied Aura token slot is malformed")
    return value[0], value[1]


@dataclass(frozen=True, slots=True)
class CopiedAuraTokenSelection:
    slot: tuple[int, int]
    ref: str
    identity: ContinuousObjectIdentity | None

    def __post_init__(self) -> None:
        if type(self.slot) is not tuple:
            raise AuraRuleError("Copied Aura token slot must be immutable")
        _copy_slot(self.slot)
        if type(self.ref) is not str or not self.ref:
            raise AuraRuleError("Copied Aura entry requires a subject reference")
        if self.identity is not None and not isinstance(self.identity, ContinuousObjectIdentity):
            raise AuraRuleError("Copied Aura entry requires a typed subject identity")

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": list(self.slot),
            "ref": self.ref,
            "identity": self.identity.to_dict() if self.identity is not None else None,
        }

    @classmethod
    def from_dict(cls, value: Any) -> CopiedAuraTokenSelection:
        if not isinstance(value, Mapping) or set(value) != {"slot", "ref", "identity"}:
            raise AuraRuleError("Copied Aura token selection fields are closed")
        identity = value["identity"]
        return cls(_copy_slot(value["slot"]), value["ref"],
                   ContinuousObjectIdentity.from_dict(identity) if identity is not None else None)

    def current_ref(self, host: Any) -> str:
        if self.identity is None:
            if self.ref not in host.active_seats:
                raise AuraRuleError("Copied Aura entry player is no longer present")
        else:
            card = host.state.cards.get(self.identity.object_id)
            if card is None or card.ref != self.ref or card.logical_object_id != self.identity.logical_object_id:
                raise AuraRuleError("Copied Aura entry subject changed incarnation")
        return self.ref


class CopiedAuraTokenChoiceRequired(AuraEntryChoiceRequired):
    def __init__(self, required: AuraEntryChoiceRequired, slot: tuple[int, int]):
        super().__init__(required.plan)
        self.slot = _copy_slot(slot)


def copied_aura_token_selections(
    item: Any, *, instruction_pointer: int | None = None,
) -> tuple[CopiedAuraTokenSelection, ...]:
    raw = item.context.get(COPIED_AURA_TOKEN_SELECTIONS)
    if raw is None:
        return ()
    if not isinstance(raw, dict) or set(raw) != {"instruction_pointer", "choices"}:
        raise AuraRuleError("Copied Aura token selection context is malformed")
    pointer, choices = raw["instruction_pointer"], raw["choices"]
    if (type(pointer) is not int or pointer < 0 or not isinstance(choices, list)
            or instruction_pointer is not None and pointer != instruction_pointer):
        raise AuraRuleError("Copied Aura token selections changed resolving instruction")
    selections = tuple(CopiedAuraTokenSelection.from_dict(value) for value in choices)
    slots = tuple(selection.slot for selection in selections)
    if slots != tuple(sorted(set(slots))):
        raise AuraRuleError("Copied Aura token selections must use distinct ordered slots")
    return selections


def clear_copied_aura_token_selections(item: Any) -> None:
    item.context.pop(COPIED_AURA_TOKEN_SELECTIONS, None)


def prepare_copied_aura_token_specs(
    host: Any,
    preview: Any,
    spec: Mapping[str, Any],
    required: AuraEntryChoiceRequired,
    spec_index: int,
    selections: dict[tuple[int, int], CopiedAuraTokenSelection],
) -> list[Mapping[str, Any]]:
    plan = required.plan
    prepared = []
    for copy_index in range(int(spec.get("quantity", 1))):
        slot = (spec_index, copy_index)
        selection = selections.pop(slot, None)
        if selection is None:
            raise CopiedAuraTokenChoiceRequired(required, slot)
        entered = prepare_aura_entry(host, preview, spec=plan.spec, controller=plan.controller,
                                     target_ref=selection.current_ref(host), resolving_as_spell=False)
        if entered.outcome is not AuraEntryOutcome.ENTER_ATTACHED:
            raise AuraRuleError("Copied Aura entry subject is no longer legal")
        prepared.append({**spec, "quantity": 1, "aura_target_ref": entered.target_ref})
    return prepared


def issue_copied_aura_token_choice(host, *, item, effect, remaining, destination, note, instruction_pointer,
                                 required: CopiedAuraTokenChoiceRequired) -> None:
    from .aura.decisions import aura_entry_choice_payload

    plan = required.plan
    candidates = {}
    by_ref = {card.ref: card for card in host.state.cards.values()}
    for ref in plan.legal_target_refs:
        if ref in host.active_seats:
            candidates[ref] = None
            continue
        card = by_ref.get(ref)
        if card is None:
            raise AuraRuleError("Copied Aura entry subject lacks a current identity")
        candidates[ref] = ContinuousObjectIdentity(card.object_id, card.logical_object_id).to_dict()
    frame = host._semantic_frame(item, instruction_pointer=instruction_pointer,
                                 locals={"copied_aura_token_slot": list(required.slot)})
    decision = host.permissions.issue(
        kind="aura.entry", role="pilot", actors=[plan.controller], allowed_actions=["choose"],
        payload_by_actor={plan.controller: aura_entry_choice_payload(plan, stack_ref=item.ref, aura_ref=item.ref)},
        continuation={
            COPIED_AURA_TOKEN_CHOICE: 1,
            "slot": list(required.slot),
            "candidates": candidates,
            "effect": deepcopy(dict(effect)),
            "remaining": deepcopy(list(remaining)),
            "destination": destination,
            "note": note,
            "semantic_frame": frame,
        },
    )
    decision.continuation["semantic_frame"]["pending_choice_id"] = decision.decision_id


def complete_copied_aura_token_choice(host, decision, *, error_type: type[Exception]) -> None:
    raw = decision.continuation
    fields = {COPIED_AURA_TOKEN_CHOICE, "slot", "candidates", "effect", "remaining",
              "destination", "note", "semantic_frame"}
    version = raw.get(COPIED_AURA_TOKEN_CHOICE)
    if set(raw) != fields or type(version) is not int or version != 1:
        raise error_type("Copied Aura token continuation fields are closed")
    frame, effect, remaining = raw["semantic_frame"], raw["effect"], raw["remaining"]
    if (not isinstance(frame, dict) or not isinstance(effect, dict) or effect.get("op") != "create_token"
            or not isinstance(remaining, list) or any(not isinstance(row, dict) for row in remaining)
            or raw["destination"] is not None and type(raw["destination"]) is not str
            or type(raw["note"]) is not str or len(decision.actors) != 1):
        raise error_type("Copied Aura token continuation is malformed")
    item = host.state.stack[-1] if host.state.stack else None
    actor = decision.actors[0]
    pointer = frame.get("instruction_pointer")
    if (item is None or item.controller != actor or actor not in host.active_seats
            or item.context.get("currently_resolving") is not True or type(pointer) is not int or pointer < 0):
        raise error_type("Copied Aura token continuation has no current resolving controller")
    try:
        slot = _copy_slot(raw["slot"])
        TokenCopyRecipeSpec.from_dict(effect.get("copy_spec"))
        expected = host._semantic_frame(item, instruction_pointer=pointer,
                                        locals={"copied_aura_token_slot": list(slot)},
                                        pending_choice_id=decision.decision_id)
        if frame != expected:
            raise AuraRuleError("Copied Aura token continuation frame changed")
        response = decision.responses[actor]
        ref = response.get("aura_target", response.get("target"))
        candidates = raw["candidates"]
        if type(ref) is not str or not isinstance(candidates, dict) or ref not in candidates:
            raise AuraRuleError("Copied Aura entry subject was not offered")
        identity = candidates[ref]
        selection = CopiedAuraTokenSelection(slot, ref,
                     ContinuousObjectIdentity.from_dict(identity) if identity is not None else None)
        selection.current_ref(host)
        prior = copied_aura_token_selections(item, instruction_pointer=pointer)
        if prior and prior[-1].slot >= slot:
            raise AuraRuleError("Copied Aura token selection already committed")
    except (ValueError, TypeError, KeyError) as exc:
        raise error_type(str(exc)) from exc
    item.context[COPIED_AURA_TOKEN_SELECTIONS] = {
        "instruction_pointer": pointer,
        "choices": [choice.to_dict() for choice in (*prior, selection)],
    }
    host._continue_resolution(stack_ref=item.ref, effects=[effect, *remaining],
                              destination=raw["destination"], note=raw["note"], instruction_pointer=pointer)
