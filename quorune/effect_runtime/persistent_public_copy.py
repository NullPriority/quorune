"""Apply one persistent copy from two independently revalidated targets."""

from __future__ import annotations

from collections.abc import Mapping

from ..entry_results import record_persistent_copy
from ..errors import GameRuleError
from ..rules.persistent_public_copy import (
    is_closed_persistent_public_copy_program, persistent_public_copy_values,
    validate_persistent_public_copy_effect,
)
from .persistent_copy import resolving_persistent_copy_item


def original_copy_permanent(host, item, ref):
    card = host._resolve_object(item.controller, ref, zones={"battlefield"})
    snapshot = item.context.get("target_snapshots", {}).get(ref)
    if (not isinstance(snapshot, Mapping) or snapshot.get("object_id") != card.object_id
            or type(snapshot.get("zone_change_counter")) is not int
            or snapshot["zone_change_counter"] != card.zone_change_counter
            or card.phased_out):
        raise GameRuleError("Persistent copy requires the original target incarnation")
    return card


def apply_persistent_public_copy(host, effect, *, actor, reason):
    try:
        validate_persistent_public_copy_effect(effect, resolved=True)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    item = resolving_persistent_copy_item(host, effect, actor=actor, kinds=("spell", "spell_copy"))
    program = host.semantics.get(item.semantic_key)
    if (item.context.get("currently_resolving") is not True
            or program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_persistent_public_copy_program(program)):
        raise GameRuleError("Persistent copy requires its trusted resolving spell")
    recipient_ref, model_ref = effect["recipient"], effect["model"]
    if (item.context.get("targets_revalidated") is not True
            or tuple(item.targets) != (recipient_ref, model_ref)
            or item.context.get("target_groups_current") != {"recipient": [recipient_ref], "model": [model_ref]}):
        raise GameRuleError("Persistent copy requires its revalidated target roles in order")
    if recipient_ref is None or model_ref is None:
        return None
    recipient = original_copy_permanent(host, item, recipient_ref)
    model = original_copy_permanent(host, item, model_ref)
    values = persistent_public_copy_values(host, model, recipient=recipient)
    record_persistent_copy(recipient, values, host=host)
    host._log(actor, "permanent.copy", f"{recipient.ref} became a copy of {model.ref}.",
              {"source": recipient.ref, "target": model.ref, "reason": reason},
              importance=2, changed_objects=[recipient.object_id])
    return recipient.ref
