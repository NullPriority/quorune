from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from ..replacement.immutable import FrozenMap, freeze_value, thaw_value
from .model import (
    AuraEnchantSubject,
    AuraEntryChoiceRequired,
    AuraRuleError,
    SimpleEnchantSpec,
    TypedEnchantSpec,
    enchant_spec_from_dict,
    enchant_spec_to_dict,
)
from .runtime import legal_aura_target_refs


_FIELDS = {
    "schema_version",
    "stack_ref",
    "source_object_id",
    "source_logical_object_id",
    "controller",
    "effect",
    "remaining",
    "destination",
    "note",
    "instruction_pointer",
    "semantic_frame",
    "spec",
    "advertised_targets",
}
_PILOT_ROLE = "pilot"


class AuraDecisionHost(Protocol):
    state: Any
    permissions: Any

    def _semantic_frame(
        self,
        item: Any,
        *,
        instruction_pointer: int,
        locals: Mapping[str, Any] | None = None,
        pending_choice_id: str | None = None,
    ) -> dict[str, Any]: ...

    def _validate_semantic_frame(
        self, frame: Mapping[str, Any], item: Any
    ) -> None: ...

    def _continue_resolution(
        self,
        *,
        stack_ref: str,
        effects: list[dict[str, Any]],
        destination: str | None,
        note: str,
        instruction_pointer: int = 0,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class AuraEntryContinuation:
    stack_ref: str
    source_object_id: str
    source_logical_object_id: str
    controller: str
    effect: Mapping[str, Any]
    remaining: tuple[Mapping[str, Any], ...]
    destination: str | None
    note: str
    instruction_pointer: int
    semantic_frame: Mapping[str, Any]
    spec: Mapping[str, Any]
    advertised_targets: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        for field_name in ("effect", "semantic_frame", "spec"):
            frozen = freeze_value(getattr(self, field_name))
            if not isinstance(frozen, FrozenMap):
                raise AuraRuleError(
                    f"Aura entry continuation {field_name} must be an object"
                )
            object.__setattr__(self, field_name, frozen)
        frozen_remaining = tuple(freeze_value(value) for value in self.remaining)
        if any(not isinstance(value, FrozenMap) for value in frozen_remaining):
            raise AuraRuleError(
                "Aura entry continuation remaining must contain objects"
            )
        object.__setattr__(self, "remaining", frozen_remaining)

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "AuraEntryContinuation":
        if set(value) != _FIELDS:
            raise AuraRuleError(
                "Aura entry continuation has missing or unknown fields"
            )
        if (
            type(value.get("schema_version")) is not int
            or value["schema_version"] != 1
        ):
            raise AuraRuleError(
                "Unsupported Aura entry continuation version"
            )
        for name in ("effect", "semantic_frame", "spec"):
            if not isinstance(value.get(name), Mapping):
                raise AuraRuleError(
                    f"Aura entry continuation {name} must be an object"
                )
        raw_remaining = value.get("remaining")
        if not isinstance(raw_remaining, list) or any(
            not isinstance(entry, Mapping) for entry in raw_remaining
        ):
            raise AuraRuleError(
                "Aura entry continuation remaining must contain objects"
            )
        raw_targets = value.get("advertised_targets")
        if not isinstance(raw_targets, list) or any(
            not isinstance(entry, str) or not entry
            for entry in raw_targets
        ):
            raise AuraRuleError(
                "Aura entry continuation targets must be nonempty refs"
            )
        if len(raw_targets) != len(set(raw_targets)):
            raise AuraRuleError(
                "Aura entry continuation targets must be unique"
            )
        instruction_pointer = value.get("instruction_pointer")
        if type(instruction_pointer) is not int or instruction_pointer < 0:
            raise AuraRuleError(
                "Aura entry instruction pointer must be nonnegative"
            )
        for name in (
            "stack_ref",
            "source_object_id",
            "source_logical_object_id",
            "controller",
        ):
            if not isinstance(value.get(name), str) or not value[name]:
                raise AuraRuleError(
                    f"Aura entry continuation {name} must be nonempty"
                )
        destination = value.get("destination")
        if destination is not None and (
            not isinstance(destination, str) or not destination
        ):
            raise AuraRuleError(
                "Aura entry continuation destination must be nonempty or null"
            )
        if not isinstance(value.get("note"), str):
            raise AuraRuleError("Aura entry continuation note must be a string")
        return cls(
            schema_version=1,
            stack_ref=str(value.get("stack_ref") or ""),
            source_object_id=str(value.get("source_object_id") or ""),
            source_logical_object_id=str(
                value.get("source_logical_object_id") or ""
            ),
            controller=str(value.get("controller") or ""),
            effect=copy.deepcopy(dict(value["effect"])),
            remaining=tuple(
                copy.deepcopy(dict(entry)) for entry in raw_remaining
            ),
            destination=destination,
            note=value["note"],
            instruction_pointer=instruction_pointer,
            semantic_frame=copy.deepcopy(
                dict(value["semantic_frame"])
            ),
            spec=copy.deepcopy(dict(value["spec"])),
            advertised_targets=tuple(raw_targets),
        )


def issue_aura_entry_choice(
    host: AuraDecisionHost,
    *,
    item: Any,
    effect: Mapping[str, Any],
    remaining: Sequence[Mapping[str, Any]],
    destination: str | None,
    note: str,
    instruction_pointer: int,
    required: AuraEntryChoiceRequired,
) -> None:
    plan = required.plan
    player_subject = bool(
        isinstance(plan.spec, TypedEnchantSpec)
        and plan.spec.subject is AuraEnchantSubject.PLAYER
    )
    frame = host._semantic_frame(
        item,
        instruction_pointer=instruction_pointer,
        locals={"aura_source": plan.source_object_id},
    )
    decision = host.permissions.issue(
        kind="aura.entry",
        role=_PILOT_ROLE,
        actors=[plan.controller],
        allowed_actions=["choose"],
        payload_by_actor={
            plan.controller: {
                "stack": item.ref,
                "aura": host.state.cards[plan.source_object_id].ref,
                "prompt": "Choose a legal subject for this Aura to enchant.",
                "target_schema": {
                    **plan.spec.target_schema(),
                    "legal_refs": list(plan.legal_target_refs),
                },
                "legal_actions": [
                    {
                        "id": "choose",
                        "action": "choose",
                        "choice_schema": {
                            "aura_target": {
                                "type": "seat" if player_subject else "object_ref",
                                "required": True,
                                **(
                                    {
                                        "legal_seats": list(
                                            plan.legal_target_refs
                                        )
                                    }
                                    if player_subject
                                    else {
                                        "legal_refs": list(
                                            plan.legal_target_refs
                                        )
                                    }
                                ),
                            }
                        },
                    }
                ],
            }
        },
        continuation={
            "schema_version": 1,
            "stack_ref": item.ref,
            "source_object_id": plan.source_object_id,
            "source_logical_object_id": (
                plan.source_logical_object_id
            ),
            "controller": plan.controller,
            "effect": copy.deepcopy(dict(effect)),
            "remaining": copy.deepcopy(list(remaining)),
            "destination": destination,
            "note": note,
            "instruction_pointer": instruction_pointer,
            "semantic_frame": frame,
            "spec": (
                plan.spec.to_dict()
                if isinstance(plan.spec, SimpleEnchantSpec)
                else enchant_spec_to_dict(plan.spec)
            ),
            "advertised_targets": list(plan.legal_target_refs),
        },
    )
    decision.continuation["semantic_frame"]["pending_choice_id"] = (
        decision.decision_id
    )


def complete_aura_entry_choice(
    host: AuraDecisionHost,
    decision: Any,
    *,
    error_type: type[Exception],
) -> None:
    if 'opening_hand_entry' in decision.continuation:
        from ..opening_hand import resume_opening_hand_aura
        resume_opening_hand_aura(host,decision)
        return
    if decision.continuation.get('conditional_aura_return') is True:
        from ..conditional_exile_return import resume_conditional_aura_choice
        resume_conditional_aura_choice(host,decision,error_type=error_type)
        return
    try:
        restored = AuraEntryContinuation.from_dict(
            decision.continuation
        )
        raw_spec = thaw_value(restored.spec)
        spec = (
            enchant_spec_from_dict(raw_spec)
            if set(raw_spec) == {"kind", "value"}
            else SimpleEnchantSpec.from_dict(raw_spec)
        )
    except AuraRuleError as exc:
        raise error_type(str(exc)) from exc
    item = next(
        (
            candidate
            for candidate in host.state.stack
            if candidate.ref == restored.stack_ref
        ),
        None,
    )
    if item is None:
        raise error_type(
            "Aura entry continuation stack object no longer exists"
        )
    host._validate_semantic_frame(
        thaw_value(restored.semantic_frame), item
    )
    source = host.state.cards.get(restored.source_object_id)
    if (
        source is None
        or source.logical_object_id
        != restored.source_logical_object_id
    ):
        raise error_type("Aura entry source identity changed")
    seat = decision.actors[0]
    if seat != restored.controller:
        raise error_type("Aura entry choice actor changed")
    current = legal_aura_target_refs(
        host,
        source,
        spec,
        controller=restored.controller,
        as_target=False,
    )
    response = decision.responses[seat]
    selected = response.get("aura_target", response.get("target"))
    from ..copied_aura_entry import complete_copied_aura_entry_choice
    if complete_copied_aura_entry_choice(host, item=item, restored=restored, source=source,
            spec=spec, selected=selected, error_type=error_type):
        return
    if (
        not isinstance(selected, str)
        or selected not in restored.advertised_targets
        or selected not in current
    ):
        raise error_type(
            "Selected Aura entry target is no longer legal"
        )
    effect = thaw_value(restored.effect)
    effect["_aura_target_ref"] = selected
    host._continue_resolution(
        stack_ref=restored.stack_ref,
        effects=[
            effect,
            *(thaw_value(value) for value in restored.remaining),
        ],
        destination=restored.destination,
        note=restored.note,
        instruction_pointer=restored.instruction_pointer,
    )


__all__ = [
    "AuraDecisionHost",
    "AuraEntryContinuation",
    "complete_aura_entry_choice",
    "issue_aura_entry_choice",
]
