"""Resolve the target announced by one retained-color upkeep ability."""

from __future__ import annotations

from ..ability_fragments import GrantedTriggeredAbilitySpec, ability_fragment_to_dict
from ..entry_copy import EntryCopySpec
from ..entry_results import record_persistent_copy
from ..errors import GameRuleError
from ..object_predicate import ObjectQuerySpec
from ..rules.color_retaining_copy import (
    RETAINED_COLOR_COPY_VERSION, RETAINED_COLOR_UPKEEP_LABEL, is_closed_retained_color_upkeep_program,
)
from ..semantic_runtime.entry_copy import retained_color_copy_values
from ..token_copy_recipes import TokenCopyExceptionSpec
from .persistent_copy import resolving_persistent_copy_source


def apply_retained_color_upkeep_copy(host, effect, *, actor, reason):
    allowed = {"op", RETAINED_COLOR_COPY_VERSION, "source", "target"}
    if (set(effect) - {"_runtime_source", "reason"} != allowed
            or effect["op"] != "copy_until_end_of_turn"
            or type(effect[RETAINED_COLOR_COPY_VERSION]) is not int or effect[RETAINED_COLOR_COPY_VERSION] != 1
            or type(effect["target"]) is not str or not effect["target"]
            or (effect["source"] is not None and (type(effect["source"]) is not str or not effect["source"]))):
        raise GameRuleError("The retained-color upkeep copy requires its closed instruction")
    resolved = resolving_persistent_copy_source(host, effect, actor=actor, kind="triggered_ability")
    if resolved is None:
        return None
    item, source = resolved
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_retained_color_upkeep_program(program)):
        raise GameRuleError("The retained-color copy requires its own trusted upkeep grant")
    if not item.context.get("targets_revalidated") or tuple(item.targets) != (effect["target"],):
        raise GameRuleError("The retained-color copy requires the revalidated announced target")
    target = host._resolve_object(actor, effect["target"], zones={"battlefield"})
    grant = ability_fragment_to_dict(GrantedTriggeredAbilitySpec(
        program.ability_id, program.key, program.event, RETAINED_COLOR_UPKEEP_LABEL,
    ))
    spec = EntryCopySpec(ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
                         schema_version=14, exception=TokenCopyExceptionSpec(add_ability_fragments=(grant,)))
    values = retained_color_copy_values(host, target, spec, entering=source)
    record_persistent_copy(source, values, host=host)
    host._log(actor, "permanent.copy", f"{source.ref} became a copy of {target.ref}.",
              {"source": source.ref, "target": target.ref, "reason": reason},
              importance=2, changed_objects=[source.object_id])
    return source.ref
