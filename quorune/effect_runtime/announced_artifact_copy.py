"""Resolve one authorized persistent copy at the activation's announced X."""

from __future__ import annotations

from ..entry_results import record_persistent_copy
from ..errors import GameRuleError
from ..replacement.immutable import thaw_value
from ..rules.announced_artifact_copy import (
    announced_artifact_copy_target_schema, is_closed_announced_artifact_copy_program,
    validate_announced_artifact_copy_effect,
)
from ..rules.persistent_public_copy import persistent_public_copy_values
from .persistent_copy import resolving_persistent_copy_source
from .persistent_public_copy import original_copy_permanent


def apply_announced_artifact_copy(host, effect, *, actor, reason):
    try:
        validate_announced_artifact_copy_effect(effect, resolved=True)
        activation = resolving_persistent_copy_source(host, effect, actor=actor, kind="activated_ability")
        if activation is None:
            return None
        item, source = activation
        program = host.semantics.get(item.semantic_key)
        target_ref = effect["target"]
        if (item.context.get("currently_resolving") is not True or program is None
                or not host.semantic_program_is_current_trusted(program)
                or not is_closed_announced_artifact_copy_program(program)):
            raise GameRuleError("An artifact copy requires its trusted resolving activation")
        if (type(item.x_value) is not int or not item.context.get("targets_revalidated")
                or tuple(item.targets) != (target_ref,)
                or thaw_value(item.context.get("target_schema_override"))
                != announced_artifact_copy_target_schema(item.x_value)):
            raise GameRuleError("The artifact copy target was not revalidated at the announced X")
        model = original_copy_permanent(host, item, target_ref)
        if model.controller != actor or model.is_token:
            raise GameRuleError("An artifact copy requires the original controlled nontoken model")
        values = persistent_public_copy_values(host, model, recipient=source)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc

    record_persistent_copy(source, values, host=host)
    host._log(actor, "permanent.copy", f"{source.ref} became a copy of {model.ref}.",
              {"source": source.ref, "target": model.ref, "reason": reason},
              importance=2, changed_objects=[source.object_id])
    return source.ref
