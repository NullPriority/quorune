"""The resolving activation and physical recipient of a persistent copy."""

from __future__ import annotations

from collections.abc import Mapping

from ..errors import GameRuleError


def resolving_persistent_copy_item(host, effect, *, actor, kinds):
    """Authenticate the stack item independently of its original permanent."""
    context = effect.get("_runtime_source")
    if not isinstance(context, Mapping):
        raise GameRuleError("A persistent copy requires its resolving stack object")
    item = next((item for item in host.state.stack if item.ref == context.get("stack_ref")), None)
    if item is None or item.kind not in kinds or item.controller != actor:
        raise GameRuleError("A persistent copy requires its resolving stack object")
    return item


def resolving_persistent_copy_source(host, effect, *, actor, kind):
    """Keep an ability on a departed source from changing its new incarnation."""
    if effect["source"] is None:
        return None
    item = resolving_persistent_copy_item(host, effect, actor=actor, kinds=(kind,))
    context = effect["_runtime_source"]
    source = host.state.cards.get(item.source_object_id)
    if (source is None or source.zone != "battlefield" or source.phased_out
            or source.logical_object_id != context.get("logical_object_id")):
        return None
    if (source.ref != effect["source"] or source.object_id != context.get("object_id")
            or source.logical_object_id != item.context.get("source_logical_object_id")):
        raise GameRuleError("The persistent copy source identity changed")
    return item, source


def persistent_copy_effect_handler(effect):
    from ..rules.announced_graveyard_copy import is_announced_graveyard_copy_effect
    from ..rules.color_retaining_copy import is_retained_color_upkeep_copy
    from ..rules.graveyard_exile_copy import is_graveyard_exile_copy_effect
    from ..rules.linked_exile_copy import is_linked_exile_copy_effect
    from ..rules.persistent_public_copy import is_persistent_public_copy_effect
    from ..rules.persistent_self_copy_trigger import is_persistent_self_copy_trigger

    if is_persistent_self_copy_trigger(effect):
        from .persistent_self_copy_trigger import apply_persistent_self_copy_trigger

        return apply_persistent_self_copy_trigger
    if is_retained_color_upkeep_copy(effect):
        from .color_retaining_copy import apply_retained_color_upkeep_copy

        return apply_retained_color_upkeep_copy
    if is_announced_graveyard_copy_effect(effect):
        from .announced_graveyard_copy import apply_announced_graveyard_copy

        return apply_announced_graveyard_copy
    if is_linked_exile_copy_effect(effect):
        from .linked_exile_copy import apply_linked_exile_copy

        return apply_linked_exile_copy
    if is_graveyard_exile_copy_effect(effect):
        from .graveyard_exile_copy import apply_graveyard_exile_copy

        return apply_graveyard_exile_copy
    if is_persistent_public_copy_effect(effect):
        from .persistent_public_copy import apply_persistent_public_copy

        return apply_persistent_public_copy
    return None
