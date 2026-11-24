"""Resolve the announced graveyard exile and then the persistent copy."""

from __future__ import annotations

from ..entry_results import record_persistent_copy
from ..errors import GameRuleError
from ..granted_activation_model import GrantedActivatedAbilitySpec
from ..rules.graveyard_exile_copy import (
    GRAVEYARD_EXILE_COPY_PRICE, graveyard_exile_copy_values,
    is_closed_graveyard_exile_copy_program, validate_graveyard_exile_copy_effect,
)
from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors
from ..zone_transitions import ZoneTransitionOwner
from .persistent_copy import resolving_persistent_copy_item, resolving_persistent_copy_source


def _retained_activation(host, item):
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_graveyard_exile_copy_program(program)):
        raise GameRuleError("A graveyard exile copy requires its trusted activation")
    abilities = activated_abilities_from_descriptors(program.handlers)
    if len(abilities) != 1:
        raise GameRuleError("A graveyard exile copy requires one retained activation")
    ability, = abilities
    if (ability.cost_text != "{1}{U}{B}" or ability.zones != ("battlefield",)
            or ability.sorcery_speed or ability.activation_conditions or ability.activation_limit is not None
            or {key: count for key, count in ability.mana.items() if count} != dict(GRAVEYARD_EXILE_COPY_PRICE)):
        raise GameRuleError("A graveyard exile copy requires its ordinary paid activation")
    return GrantedActivatedAbilitySpec(
        f"{ability.ability_id}:graveyard-exile-copy", program.key, ability.cost_text, ability.effect_text,
        mana=GRAVEYARD_EXILE_COPY_PRICE,
    )


def apply_graveyard_exile_copy(host, effect, *, actor, reason):
    try:
        validate_graveyard_exile_copy_effect(effect, resolved=True)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    kind = "activated_ability"
    item = resolving_persistent_copy_item(host, effect, actor=actor, kinds=(kind,))
    if (item.context.get("currently_resolving") is not True
            or item.context.get("targets_revalidated") is not True
            or tuple(item.targets) != (effect["target"],)):
        raise GameRuleError("A graveyard exile copy requires its revalidated announced card")
    retained = _retained_activation(host, item)
    target = host._resolve_object(actor, effect["target"], zones={"graveyard"})
    if not target.is_card_object or target.face_down:
        raise GameRuleError("A graveyard exile copy requires a public physical card")
    source = resolving_persistent_copy_source(host, effect, actor=actor, kind=kind)
    values = (graveyard_exile_copy_values(host, target, receiver=source[1], retained_ability=retained)
              if source is not None else None)
    with host.transaction():
        moved = ZoneTransitionOwner(host).move_card(
            target.object_id, "exile", reason=reason, semantic_events=True,
            replacement_selections=tuple(effect.get("_replacement_selections") or ()),
        )
        current = resolving_persistent_copy_source(host, effect, actor=actor, kind=kind)
        if values is None or current is None:
            return None
        _, recipient = current
        if moved.zone == "exile" and not moved.face_down:
            values = graveyard_exile_copy_values(host, moved, receiver=recipient, retained_ability=retained)
        record_persistent_copy(recipient, values, host=host)
        host._log(actor, "permanent.copy", f"{recipient.ref} became a copy of {target.ref}.",
                  {"source": recipient.ref, "target": target.ref, "reason": reason},
                  importance=2, changed_objects=[recipient.object_id])
        return recipient.ref
