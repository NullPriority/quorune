"""Resolve a named persistent copy using the activation's announced target."""

from __future__ import annotations

from ..entry_results import record_persistent_copy
from ..errors import GameRuleError
from ..granted_activation_model import GrantedActivatedAbilitySpec
from ..replacement.immutable import thaw_value
from ..rules.announced_graveyard_copy import (
    announced_graveyard_copy_target_schema, is_closed_announced_graveyard_copy_program,
    prepare_announced_graveyard_copy_values, validate_announced_graveyard_copy_effect,
)
from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors
from .persistent_copy import resolving_persistent_copy_source


def _retained_ability(host, item, name):
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_announced_graveyard_copy_program(program)
            or program.effects[0]["retained_name"] != name):
        raise GameRuleError("A graveyard copy requires its trusted named activation")
    matches = activated_abilities_from_descriptors(program.handlers)
    if len(matches) != 1:
        raise GameRuleError("A graveyard copy requires one source-pinned activation")
    ability, = matches
    if (ability.cost_text != "{X}" or ability.announced_x_symbols != 1 or ability.zones != ("battlefield",)
            or ability.sorcery_speed or ability.activation_conditions or ability.activation_limit is not None):
        raise GameRuleError("A graveyard copy requires its ordinary X activation")
    return GrantedActivatedAbilitySpec(
        f"{ability.ability_id}:announced-graveyard-copy", program.key, ability.cost_text, ability.effect_text,
        announced_x_symbols=1, schema_version=7,
    )


def _graveyard_copy_target(host, item, ref):
    expected = announced_graveyard_copy_target_schema(item.x_value)
    if (thaw_value(item.context.get("target_schema_override")) != expected
            or not item.context.get("targets_revalidated") or tuple(item.targets) != (ref,)):
        raise GameRuleError("The copy target was not revalidated at the announced X")
    target = host._resolve_object(item.controller, ref, zones={"graveyard"})
    if target.owner != item.controller or not target.is_card_object or target.face_down:
        raise GameRuleError("The copy target left the acting player's public graveyard")
    return target


def apply_announced_graveyard_copy(host, effect, *, actor, reason):
    try:
        validate_announced_graveyard_copy_effect(effect, resolved=True)
        activation = resolving_persistent_copy_source(host, effect, actor=actor, kind="activated_ability")
        if activation is None:
            return None
        item, source = activation
        target = _graveyard_copy_target(host, item, effect["target"])
        name = effect["retained_name"]
        retained = _retained_ability(host, item, name)
        values = prepare_announced_graveyard_copy_values(host, target, name=name, retained_ability=retained)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    record_persistent_copy(source, values, host=host)
    host._log(actor, "permanent.copy", f"{source.ref} became a copy of {target.ref}.",
              {"source": source.ref, "target": target.ref, "reason": reason},
              importance=2, changed_objects=[source.object_id])
    return source.ref
