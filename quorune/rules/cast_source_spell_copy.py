from __future__ import annotations

"""Optional copies of the observed cast spell with one fixed receiver."""

from collections.abc import Mapping

from ..errors import GameRuleError
from ..event_spell_copy_model import EVENT_SPELL_COPY_CAPABILITY, EVENT_SPELL_COPY_CONTEXT, event_spell_copy_effect
from ..replacement.immutable import FrozenMap, thaw_value
from .cast_target_copy import sole_cast_target


SOURCE_CAST_COPY_FIELD = "fixed_source_cast_copy_version"
SOURCE_CAST_COPY_CAPABILITY = "stack.copy.cast_event_spell_to_source"
SOURCE_CAST_COPY_CONDITION_FIELD = "sole_cast_creature_target_other_than_source"


def source_cast_copy_condition():
    return {"field": SOURCE_CAST_COPY_CONDITION_FIELD, "op": "truthy", "value": True}


def source_cast_copy_effect():
    return {**event_spell_copy_effect(), SOURCE_CAST_COPY_FIELD: 1}


def source_cast_copy_program_dependencies(program):
    expected = FrozenMap(source_cast_copy_effect())
    if (
        len(program.effects) != 1 or FrozenMap(program.effects[0]) != expected
        or type(program.effects[0].get(SOURCE_CAST_COPY_FIELD)) is not int
        or program.event != "spell.cast" or program.active_zone != "battlefield"
        or program.target_schema is not None or getattr(program, "cost_schema", None) is not None
        or FrozenMap(program.event_condition or {}) != FrozenMap(source_cast_copy_condition())
        or "intervening_condition" in program.coverage
    ):
        raise ValueError("Source-target spell copy requires its original untargeted cast trigger")
    return (
        SOURCE_CAST_COPY_CAPABILITY, EVENT_SPELL_COPY_CAPABILITY, "effect.choice.optional_fixed",
        "trigger.event.normalized_spell_cast", "trigger.effect.fixed_event", "trigger.placement.apnap",
        "target.revalidate_resolution",
    )


def source_cast_copy_condition_holds(host, condition, context, source):
    if condition.get("value") is not True or dict(condition) != source_cast_copy_condition():
        raise ValueError("Source-target spell copy requires its exact cast predicate")
    target = sole_cast_target(host, context)
    if target is None or target.object_id == source.object_id:
        return False
    types, _, _ = host._type_parts(str(host._effective_card_data(target).get("type_line") or ""))
    return "creature" in types


def original_cast_copy_program(host, effect, actor, trigger, dependencies):
    if (
        trigger is None or trigger.context.get("event") != "spell.cast"
        or effect.get("op") != "copy_stack_item" or effect.get("stack") != trigger.context.get("stack")
        or not isinstance(effect.get("copy_source"), Mapping)
        or not isinstance(trigger.context.get(EVENT_SPELL_COPY_CONTEXT), Mapping)
        or FrozenMap(effect["copy_source"]) != FrozenMap(trigger.context[EVENT_SPELL_COPY_CONTEXT])
        or effect.get("_choice_actor", actor) != actor
    ):
        raise GameRuleError("Fixed copy lost its original cast source or actor")
    program = host.semantics.get(trigger.semantic_key)
    try:
        if program is None or not set(dependencies(program)) <= set(program.capability_dependencies):
            raise ValueError("Fixed copy requires its complete capability-bound cast trigger")
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    return program


def _validate_source_cast_copy(host, effect, actor, trigger):
    original_cast_copy_program(host, effect, actor, trigger, source_cast_copy_program_dependencies)
    fields = set(effect) - {"_choice_actor", "_stack_label", "_runtime_source", "reason"}
    if (
        trigger.controller != actor or fields != set(source_cast_copy_effect())
        or type(effect.get(SOURCE_CAST_COPY_FIELD)) is not int or effect[SOURCE_CAST_COPY_FIELD] != 1
    ):
        raise GameRuleError("Source-target copy lost its original effect or ability controller")


def fixed_cast_copy_assignment(host, spell, schema, ref, *, actor, acting_source, source_ref=None):
    """Validate a new assignment in every occupied slot of the original spell."""
    from ..selection.copy_targets import prepare_copy_targets

    if not spell.targets or not isinstance(schema, Mapping):
        raise GameRuleError("Fixed copy lost the original spell's announced target slots")
    try:
        return prepare_copy_targets(
            host, actor=actor, schema=schema, modes=spell.modes, source_ref=source_ref,
            original_targets=spell.targets, original_groups=spell.context.get("target_groups") or {},
            original_snapshots=spell.context.get("target_snapshots") or {},
            submitted=[{"ref": ref} for _ in spell.targets],
            acting_source=acting_source, record_optimization=False,
        )
    except GameRuleError:
        return None


def materialize_source_cast_copy(host, effect, *, actor, trigger):
    """Preview one fixed assignment using the same copy validator as commitment."""
    from .event_spell_copy import resolve_event_spell_copy_source
    from ..target_source import TargetingSource

    _validate_source_cast_copy(host, effect, actor, trigger)
    receiver = host.state.cards.get(trigger.source_object_id)
    if (
        receiver is None or receiver.zone != "battlefield" or receiver.phased_out
        or receiver.logical_object_id != trigger.context.get("source_logical_object_id")
    ):
        return {}, {}
    bound = resolve_event_spell_copy_source(host, effect)
    target = bound.item()
    schema = thaw_value(bound.target_schema)
    assignment = fixed_cast_copy_assignment(
        host, target, schema, receiver.ref, actor=actor,
        acting_source=TargetingSource.from_characteristics("spell", bound.characteristics, controller=actor),
    )
    if assignment is None:
        return {}, {}
    key = f"{actor}:{target.ref}"
    return {
        key: {"authoritative": dict(schema), "public": {}},
    }, {
        key: {"targets": list(assignment.targets), "groups": thaw_value(assignment.groups),
              "snapshots": thaw_value(assignment.snapshots)},
    }
