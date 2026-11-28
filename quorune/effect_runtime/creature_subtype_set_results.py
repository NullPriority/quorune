from __future__ import annotations

"""Commit a closed subtype result on its resolution-time set."""

from ..continuous_effect_state import (
    ContinuousEffectStateError, ResolutionEffectSource, create_resolution_continuous_effect_components,
    matching_battlefield_objects,
)
from ..errors import GameRuleError
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from ..rules.creature_subtype_set_results import (
    CREATURE_SUBTYPE_SET_RESULT_VERSION, CreatureSubtypeSetResult, creature_subtype_set_runtime_dependencies,
    is_paid_controller_subtype_set_result, paid_controller_subtype_set_components,
    paid_controller_subtype_set_runtime_dependencies, validate_paid_controller_subtype_set_effect,
)


def _fixed_subtype_subjects(host, item, spec, selected, *, actor):
    targets = tuple(ref for ref in item.targets if ref is not None)
    if spec.scope == "target_players_creatures":
        if targets != (selected,) or selected not in host.active_seats:
            raise GameRuleError("The subtype result requires its revalidated target player")
        return matching_battlefield_objects(host, ObjectQuerySpec(
            zones=("battlefield",), types_all=("creature",), controller=selected,
        ))
    if not isinstance(selected, (tuple, list)) or targets != tuple(selected):
        raise GameRuleError("The subtype result requires its revalidated creature targets")
    return tuple(host._resolve_object(actor, ref, zones={"battlefield"}) for ref in targets)


def _apply_paid_controller_subtype_set(host, effect, *, actor, reason):
    try:
        validate_paid_controller_subtype_set_effect(effect, resolved=True)
        source = ResolutionEffectSource.from_effect(effect)
        item = next((row for row in host.state.stack if row.ref == source.stack_ref), None)
        if (item is None or item.kind != "activated_ability" or item.controller != actor or item.targets
                or item.context.get("currently_resolving") is not True
                or type(item.x_value) is not int or item.x_value < 0):
            raise ValueError("Paid controller types and base stats require their resolving announced X")
        program = host.semantics.get(item.semantic_key)
        if (program is None or not host.semantic_program_is_current_trusted(program)
                or not paid_controller_subtype_set_runtime_dependencies(program)):
            raise ValueError("Paid controller types and base stats require their trusted activation")
        affected = matching_battlefield_objects(host, ObjectQuerySpec(
            zones=("battlefield",), types_all=("creature",), controller=actor,
        ))
        if not affected:
            return []
        create_resolution_continuous_effect_components(
            host, source=source, targets=affected, components=paid_controller_subtype_set_components(item.x_value),
        )
    except (ContinuousEffectStateError, KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    refs = [card.ref for card in affected]
    host._log(actor, "permanent.characteristics", "Creature types and base stats changed until end of turn.",
              {"objects": refs, "subtype_set": "all", "base": item.x_value, "reason": reason},
              importance=1, changed_objects=[card.object_id for card in affected])
    return refs


def apply_creature_subtype_set_result(host, effect, *, actor, reason):
    if is_paid_controller_subtype_set_result(effect):
        return _apply_paid_controller_subtype_set(host, effect, actor=actor, reason=reason)
    try:
        spec = CreatureSubtypeSetResult.from_mapping(effect.get("subtype_set_result"))
        fields = set(spec.to_instruction())
        if (set(effect) - {"_runtime_source", "reason"} != fields
                or type(effect.get("schema_version")) is not int
                or effect["schema_version"] != CREATURE_SUBTYPE_SET_RESULT_VERSION):
            raise ValueError("Invalid subtype set result version or fields")
        source = ResolutionEffectSource.from_effect(effect)
    except (ValueError, TypeError) as exc:
        raise GameRuleError(str(exc)) from exc
    item = next((row for row in host.state.stack if row.ref == source.stack_ref), None)
    if (
        item is None or item.kind not in {"spell", "spell_copy", "activated_ability"} or item.controller != actor
        or item.context.get("currently_resolving") is not True or item.context.get("targets_revalidated") is not True
    ):
        raise GameRuleError("The subtype result requires its resolving stack frame")
    program = host.semantics.get(item.semantic_key)
    if (
        program is None or not host.semantic_program_is_current_trusted(program)
        or thaw_value(program.effects) != [spec.to_instruction()]
        or not creature_subtype_set_runtime_dependencies(program)
    ):
        raise GameRuleError("The subtype result requires its closed trusted program")
    affected = _fixed_subtype_subjects(host, item, spec, effect["subjects"], actor=actor)
    if not affected:
        return []
    try:
        create_resolution_continuous_effect_components(host, source=source, targets=affected, components=spec.components())
    except ContinuousEffectStateError as exc:
        raise GameRuleError(str(exc)) from exc
    refs = [card.ref for card in affected]
    host._log(actor, "permanent.characteristics", "Creature types changed until end of turn.",
              {"objects": refs, "subtype_set": spec.subtype_set, "reason": reason},
              importance=1, changed_objects=[card.object_id for card in affected])
    return refs
