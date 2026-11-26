"""One-point redirection branches inside the simultaneous replacement batch."""

from dataclasses import replace
from hashlib import sha256
import json

from .immutable import thaw_value
from .model import ReplacementClass, ReplacementEffectError
from .operations import RedirectDamage


DAMAGE_SPLIT_ORIGIN = "damage_split_origin"


def finite_damage_redirection(effect):
    for operation in effect.operations:
        if isinstance(operation, RedirectDamage) and operation.maximum_damage is not None:
            return operation
    return None


def validate_finite_damage_redirection(effect, operations, declines):
    if any(isinstance(op, RedirectDamage) and op.maximum_damage is not None for op in (*operations, *declines)) and (
        effect.event_kind != "damage" or effect.replacement_class is not ReplacementClass.OTHER
        or effect.optional or len(operations) != 1 or declines
    ):
        raise ReplacementEffectError("Finite damage redirection is one mandatory damage replacement")


def same_damage_recipient(event, operation):
    return (
        event.payload.get("target_kind") == operation.target_kind == "permanent"
        and event.payload.get("target_object_id") == operation.target_object_id
        and event.payload.get("target_logical_object_id") == operation.target_logical_object_id
    )


def split_finite_redirection(pending, effect, operation, effects):
    """Return two canonical damage roots when one point changes recipients."""
    event = pending.choice.event
    if pending.path or event.kind != "damage" or event.payload.get("target_kind") != "permanent":
        raise ReplacementEffectError("Finite redirection requires an original permanent-damage event")
    amount = event.payload.get("amount")
    if type(amount) is not int or amount <= 0:
        raise ReplacementEffectError("Finite redirection requires positive damage")
    if amount == operation.maximum_damage or same_damage_recipient(event, operation):
        return None

    payload = thaw_value(event.payload)
    history = dict(payload.get("replacement_choosers") or {})
    history[effect.effect_id] = event.chooser
    remainder = replace(
        event,
        payload={**payload, "amount": amount - operation.maximum_damage, "replacement_choosers": history},
        applied_effects=(*event.applied_effects, effect.effect_id),
    )
    # Prior prevention and its result children belong to the original branch.
    from .operations import UNPREVENTABLE_DAMAGE_ATTEMPTS

    payload.pop("prevention_applied", None)
    payload.pop(UNPREVENTABLE_DAMAGE_ATTEMPTS, None)
    payload.update(amount=operation.maximum_damage, proposed_amount=0, prevented=0)
    payload[DAMAGE_SPLIT_ORIGIN] = payload.get(DAMAGE_SPLIT_ORIGIN, event.event_id)
    branch_key = json.dumps([event.event_id, effect.effect_id], separators=(",", ":"))
    branch = replace(event, event_id="damage.redirect:" + sha256(branch_key.encode("utf-8")).hexdigest(),
                     payload=payload, children=())
    from .application import apply_replacement

    redirected = apply_replacement(replace(pending.choice, event=branch), effects, effect.effect_id)
    return remainder, redirected


def coalesce_redirected_damage(events):
    """Damage from one original occurrence can reconverge before it is dealt."""
    if not any(DAMAGE_SPLIT_ORIGIN in event.payload for event in events):
        return events
    grouped = {}
    for event in events:
        payload = event.payload
        key = (payload.get(DAMAGE_SPLIT_ORIGIN, event.event_id), payload.get("target_kind"),
               payload.get("target_logical_object_id") or payload.get("target"))
        original = grouped.get(key)
        if original is None:
            grouped[key] = event
            continue
        combined = thaw_value(original.payload)
        for amount in ("proposed_amount", "amount", "prevented"):
            combined[amount] += payload[amount]
        grouped[key] = replace(original, payload=combined,
                               children=(*original.children, *event.children),
                               applied_effects=tuple(dict.fromkeys((*original.applied_effects, *event.applied_effects))))
    return tuple(grouped.values())
