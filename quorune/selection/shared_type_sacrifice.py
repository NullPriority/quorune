"""Resolve the two admitted shared-type choices through the APNAP owner."""

from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap
from ..rules.shared_type_sacrifice import (
    SHARED_TYPE_SACRIFICE, SacrificedCardTypes, shared_type_sacrifice_effect, validate_shared_type_sacrifice,
)


def validate_shared_sacrifice_occurrence(host, effect, continuation):
    item = next((item for item in host.state.stack if item.ref == continuation.get("stack_ref")), None)
    if (
        item is None or item.targets or item.controller not in host.active_seats
        or item.kind not in {"triggered_ability", "triggered_ability_copy"}
        or item.context.get("currently_resolving") is not True
        or (item.context.get("event"), item.context.get("step")) != ("step.begin", "end_step")
    ):
        raise GameRuleError("Shared sacrifice requires its original resolving end-step trigger")
    # Canonical stack copies can retain the original triggered-ability kind.
    copied_from = item.context.get("copied_from_stack")
    is_copy = "copied_from_stack" in item.context or item.kind == "triggered_ability_copy"
    if is_copy:
        if type(copied_from) is not str or not copied_from or copied_from == item.ref:
            raise GameRuleError("Shared sacrifice copy requires its original stack reference")
    elif item.context.get("player") != item.controller:
        raise GameRuleError("Shared sacrifice requires the original controller's end step")
    try:
        program = host.semantics.get(item.semantic_key)
        if program is None or FrozenMap(program.effects[0]) != FrozenMap(shared_type_sacrifice_effect()):
            raise ValueError("Shared sacrifice lost its original typed instruction")
        return validate_shared_type_sacrifice(effect, actor=item.controller)
    except (IndexError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc


def shared_sacrifice_followup(host, effect, *, actor, selected, declining):
    if SHARED_TYPE_SACRIFICE not in effect:
        return ()
    if effect["players"] == "opponents":
        return ({"op": "draw", "player": actor, "count": len(declining), "private": True},) if declining else ()
    if not selected:
        return ()
    ref, = selected
    # Seal effective battlefield types before movement or replacement. The
    # ordinary commit instruction must finish before this next choice starts.
    card = host._resolve_object(actor, ref, zones={"battlefield"}, controlled_only=True)
    public = host._public_object_query_result(card)
    try:
        paid = SacrificedCardTypes(tuple(sorted(public.types)))
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    return (shared_type_sacrifice_effect(actor=actor, paid=paid),)
