from __future__ import annotations

"""Apply a persistent self-copy with the resolving trigger as a copiable grant."""

from ..ability_fragments import GrantedTriggeredAbilitySpec, StaticComponentSpec, ability_fragment_to_dict
from ..entry_results import record_persistent_copy
from ..errors import GameRuleError
from ..rules.persistent_public_copy import persistent_public_copy_values
from ..rules.persistent_self_copy_trigger import (
    is_closed_persistent_self_copy_trigger, validate_self_copy_upkeep_instruction,
)
from ..token_copy_recipes import TokenCopyExceptionSpec
from .persistent_copy import resolving_persistent_copy_source
from .persistent_public_copy import original_copy_permanent


def apply_persistent_self_copy_trigger(host, effect, *, actor, reason):
    try:
        validate_self_copy_upkeep_instruction(effect, resolved=True)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    resolved = resolving_persistent_copy_source(host, effect, actor=actor, kind="triggered_ability")
    if resolved is None:
        return None
    item, source = resolved
    program = host.semantics.get(item.semantic_key)
    if (
        program is None or not host.semantic_program_is_current_trusted(program)
        or not is_closed_persistent_self_copy_trigger(program)
        or item.context.get("targets_revalidated") is not True or tuple(item.targets) != (effect["target"],)
    ):
        raise GameRuleError("Self-copy requires its trusted resolving trigger and revalidated target")
    model = original_copy_permanent(host, item, effect["target"])
    if model.logical_object_id == source.logical_object_id:
        raise GameRuleError("The copy model must be another creature")
    grant = GrantedTriggeredAbilitySpec(program.ability_id, program.key, program.event, item.label)
    current_ability = StaticComponentSpec(program.key)
    exception = TokenCopyExceptionSpec(add_ability_fragments=(
        ability_fragment_to_dict(grant), ability_fragment_to_dict(current_ability),
    ))
    values = persistent_public_copy_values(host, model, recipient=source, exception=exception)
    record_persistent_copy(source, values, host=host)
    host._log(actor, "permanent.copy", f"{source.ref} became a copy of {model.ref}.",
              {"source": source.ref, "target": model.ref, "reason": reason},
              importance=2, changed_objects=[source.object_id])
    return source.ref
