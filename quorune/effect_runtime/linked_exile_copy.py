"""Commit one source-pinned activated copy of a linked creature card."""

from __future__ import annotations

from ..continuous_effect_model import ContinuousObjectIdentity
from ..entry_results import record_persistent_copy
from ..errors import GameRuleError
from ..granted_activation_model import GrantedActivatedAbilitySpec
from ..rules.linked_exile_copy import (
    capture_linked_exile_copy_target, linked_exile_copy_runtime_dependencies,
    prepare_linked_exile_copy_values, validate_linked_exile_copy_effect,
)
from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors
from .persistent_copy import resolving_persistent_copy_source


def _retained_activation(host, item):
    program = host.semantics.get(item.semantic_key)
    if program is None or not host.semantic_program_is_current_trusted(program):
        raise GameRuleError("Linked exile copy requires its current trusted ability")
    required = linked_exile_copy_runtime_dependencies(program)
    if not required or not set(required).issubset(program.capability_dependencies):
        raise GameRuleError("Linked exile copy lost its capability closure")
    matches = activated_abilities_from_descriptors(program.handlers)
    if len(matches) != 1:
        raise GameRuleError("Linked exile copy requires one source-pinned activation")
    ability, = matches
    if (ability.cost_text != "{2}" or ability.zones != ("battlefield",) or ability.sorcery_speed
            or ability.activation_conditions or ability.activation_limit is not None
            or {key: value for key, value in ability.mana.items() if value} != {"GENERIC": 2}):
        raise GameRuleError("Linked exile copy requires its ordinary two-mana activation")
    return GrantedActivatedAbilitySpec(
        f"{ability.ability_id}:linked-exile-copy", program.key, ability.cost_text, ability.effect_text,
        mana=(("GENERIC", 2),),
    )


def apply_linked_exile_copy(host, effect, *, actor, reason):
    """Use the stack source and targets captured by ordinary activation."""
    try:
        validate_linked_exile_copy_effect(effect, resolved=True)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    activation = resolving_persistent_copy_source(host, effect, actor=actor, kind="activated_ability")
    if activation is None:
        return None
    item, source = activation
    captured = capture_linked_exile_copy_target(
        host, source=ContinuousObjectIdentity(source.object_id, source.logical_object_id),
        ability_pair=effect["ability_pair"], target_ref=effect["target"],
    )
    retained = _retained_activation(host, item)
    values = prepare_linked_exile_copy_values(host, captured, retained_ability=retained)
    if values is None:
        return None
    record_persistent_copy(source, values, host=host)
    host._log(actor, "permanent.copy", f"{source.ref} became a copy of {captured.exiled.ref}.",
              {"source": source.ref, "target": captured.exiled.ref, "reason": reason},
              importance=2, changed_objects=[source.object_id])
    return source.ref
