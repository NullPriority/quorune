from __future__ import annotations

"""Pure choice vocabulary for one destruction snapshot, before coupled commit."""

from dataclasses import dataclass
from typing import Sequence

from .replacement.model import AffectedObject, ReplaceableEvent, ReplacementClass, ReplacementEffect, ReplacementEventBatch
from .replacement.operations import SetField
from .umbra_armor_model import UmbraArmorProtection


@dataclass(frozen=True, slots=True)
class DestructionReplacementSubject:
    object_id: str
    object_ref: str
    logical_object_id: str
    owner: str
    controller: str
    indestructible: bool = False
    shield_counters: int = 0
    regeneration_shields: int = 0

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in (
            self.object_id, self.object_ref, self.logical_object_id, self.owner, self.controller,
        )):
            raise ValueError("Destruction replacement subject identity is malformed")
        if type(self.indestructible) is not bool:
            raise ValueError("Destruction indestructibility must be boolean")
        if any(type(value) is not int or value < 0 for value in (self.shield_counters, self.regeneration_shields)):
            raise ValueError("Destruction replacement resources must be nonnegative integers")


@dataclass(frozen=True, slots=True)
class DestructionReplacementOptions:
    batch: ReplacementEventBatch
    effects: tuple[ReplacementEffect, ...]


def destruction_replacement_options(
    subjects: Sequence[DestructionReplacementSubject],
    protections: Sequence[UmbraArmorProtection],
    *, batch_id: str, apnap_order: Sequence[str], cause: str,
    regeneration_prohibited: bool = False,
) -> DestructionReplacementOptions:
    """Describe competing protections; this is not a mutation or commit plan."""
    if cause not in {"effect", "state_based_action"} or type(regeneration_prohibited) is not bool:
        raise ValueError("Destruction replacement cause or regeneration prohibition is malformed")
    if cause == "state_based_action" and regeneration_prohibited:
        raise ValueError("State-based destruction cannot prohibit regeneration")
    supplied = tuple(subjects)
    if any(not isinstance(subject, DestructionReplacementSubject) for subject in supplied):
        raise ValueError("Destruction replacement options require typed subjects")
    by_id = {subject.object_id: subject for subject in supplied}
    if len(by_id) != len(supplied):
        raise ValueError("Destruction replacement subjects must be distinct")
    if any(not isinstance(protection, UmbraArmorProtection) for protection in protections):
        raise ValueError("Destruction replacement options require typed Aura relationships")
    events = []
    effects = []
    for subject in sorted(supplied, key=lambda value: value.object_id):
        events.append(ReplaceableEvent(
            event_id="destroy:" + subject.logical_object_id, kind="permanent.destroy",
            affected_player=None, affected_object=AffectedObject(subject.object_id, subject.owner, subject.controller),
            payload={"object_id": subject.object_id, "logical_object_id": subject.logical_object_id,
                     "disposition": "indestructible" if subject.indestructible else "destroy",
                     "cause": cause, "clear_damage": False, "umbra_aura_object_id": None},
        ))
        condition = {"object_id": subject.object_id, "disposition": "destroy"}
        choices = []
        if subject.regeneration_shields and not regeneration_prohibited:
            choices.append(("regeneration", subject.object_id, "Regenerate " + subject.object_ref,
                            (SetField("disposition", "regeneration"),)))
        if subject.shield_counters and cause == "effect":
            choices.append(("shield", subject.object_id, "Remove a shield counter from " + subject.object_ref,
                            (SetField("disposition", "shield_counter"),)))
        for protection in protections:
            if protection.recipient_object_id != subject.object_id:
                continue
            if (protection.recipient_logical_object_id != subject.logical_object_id
                    or protection.recipient_controller != subject.controller):
                raise ValueError("Umbra armor recipient snapshot is stale")
            choices.append((f"umbra:{protection.aura_logical_object_id}:{protection.instance}",
                            protection.aura_object_id, "Use Umbra armor from " + protection.aura_ref,
                            (SetField("disposition", "umbra_armor"), SetField("clear_damage", True),
                             SetField("umbra_aura_object_id", protection.aura_object_id),
                             SetField("umbra_aura_logical_object_id", protection.aura_logical_object_id))))
        for suffix, source_id, label, operations in choices:
            effects.append(ReplacementEffect(
                effect_id=subject.logical_object_id + ":" + suffix, source_id=source_id,
                event_kind="permanent.destroy", replacement_class=ReplacementClass.OTHER,
                conditions=condition, operations=operations, label=label,
            ))
    return DestructionReplacementOptions(
        ReplacementEventBatch(batch_id, tuple(events), tuple(apnap_order)), tuple(effects),
    )
