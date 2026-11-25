from __future__ import annotations

"""One fixed spell copy per eligible creature controlled by the designated player."""

from collections.abc import Mapping

from ..errors import GameRuleError
from ..event_spell_copy_model import EVENT_SPELL_COPY_CAPABILITY, event_spell_copy_effect
from ..replacement.immutable import FrozenMap, thaw_value
from ..target_source import TargetingSource, stack_targeting_source
from .cast_source_spell_copy import fixed_cast_copy_assignment, original_cast_copy_program
from .cast_target_copy import sole_cast_target


CAST_CREATURE_SET_COPY_FIELD = "cast_creature_set_copy"
CAST_CREATURE_SET_COPY_CAPABILITY = "stack.copy.cast_event_spell_for_creature_set"
CAST_CREATURE_SET_COPY_CONDITION = "sole_cast_instant_sorcery_target_is_source"
ABILITY_CONTROLLER = "ability_controller"
OBSERVED_CASTER = "observed_caster"
_ACTOR_REFERENCE = {ABILITY_CONTROLLER: "$controller", OBSERVED_CASTER: "$context.controller"}


def cast_creature_set_copy_condition(scope):
    return {"field": CAST_CREATURE_SET_COPY_CONDITION, "op": "eq", "value": scope}


def cast_creature_set_copy_effect(scope):
    return {
        **event_spell_copy_effect(),
        "player": _ACTOR_REFERENCE[scope],
        CAST_CREATURE_SET_COPY_FIELD: {"schema_version": 1, "controller_scope": scope},
    }


def _copy_scope(effect):
    raw = effect.get(CAST_CREATURE_SET_COPY_FIELD)
    if (
        not isinstance(raw, Mapping) or set(raw) != {"schema_version", "controller_scope"}
        or type(raw["schema_version"]) is not int or raw["schema_version"] != 1
        or type(raw["controller_scope"]) is not str or raw["controller_scope"] not in _ACTOR_REFERENCE
    ):
        raise ValueError("Creature-set spell copy requires its exact controller scope and version")
    return raw["controller_scope"]


def cast_creature_set_copy_program_dependencies(program):
    if len(program.effects) != 1:
        raise ValueError("Creature-set spell copy must be the complete trigger result")
    scope = _copy_scope(program.effects[0])
    if (
        FrozenMap(program.effects[0]) != FrozenMap(cast_creature_set_copy_effect(scope))
        or program.event != "spell.cast" or program.active_zone != "battlefield"
        or program.target_schema is not None or getattr(program, "cost_schema", None) is not None
        or FrozenMap(program.event_condition or {}) != FrozenMap(cast_creature_set_copy_condition(scope))
        or "intervening_condition" in program.coverage
    ):
        raise ValueError("Creature-set spell copy requires its original untargeted cast trigger")
    return (
        CAST_CREATURE_SET_COPY_CAPABILITY, EVENT_SPELL_COPY_CAPABILITY,
        "trigger.event.normalized_spell_cast", "trigger.effect.fixed_event", "trigger.placement.apnap",
        "target.revalidate_resolution",
    )


def cast_creature_set_copy_condition_value(host, condition, context, source):
    scope = condition.get("value")
    if type(scope) is not str or scope not in _ACTOR_REFERENCE or dict(condition) != cast_creature_set_copy_condition(scope):
        raise ValueError("Creature-set spell copy requires its exact cast predicate")
    if not {"instant", "sorcery"}.intersection(context.get("types", ())):
        return None
    if scope == ABILITY_CONTROLLER and context.get("controller") != source.controller:
        return None
    target = sole_cast_target(host, context)
    return scope if target is not None and target.logical_object_id == source.logical_object_id else None


def _validate_copy(host, effect, actor, trigger):
    program = original_cast_copy_program(host, effect, actor, trigger, cast_creature_set_copy_program_dependencies)
    scope = _copy_scope(program.effects[0])
    expected = cast_creature_set_copy_effect(scope)
    fields = set(effect) - {"_copy_receiver_ids", "_choice_actor", "_stack_label", "_runtime_source", "reason"}
    source_identity = trigger.context.get("source_logical_object_id")
    expected_actor = trigger.controller if scope == ABILITY_CONTROLLER else trigger.context.get("controller")
    if (
        fields != set(expected) or _copy_scope(effect) != scope or actor != expected_actor
        or effect.get("player") != actor or type(source_identity) is not str or not source_identity
    ):
        raise GameRuleError("Creature-set copy lost its original trigger, source incarnation or copying player")
    return source_identity


def materialize_cast_creature_set_copy(host, effect, *, actor, trigger):
    from .event_spell_copy import resolve_event_spell_copy_source

    source_identity = _validate_copy(host, effect, actor, trigger)
    if actor not in host.active_seats:
        return {}, {}
    bound = resolve_event_spell_copy_source(host, effect)
    spell = bound.item()
    schema = thaw_value(bound.target_schema)
    copy_source = TargetingSource.from_characteristics("spell", bound.characteristics, controller=actor, spell_owner=actor)
    original_source = (
        TargetingSource.from_characteristics(
            "spell", bound.characteristics, controller=spell.controller,
            spell_owner=trigger.context.get("owner"), logical_object_id=bound.logical_object_id,
        )
        if bound.departed else stack_targeting_source(host, spell)
    )
    original_ref = None if bound.departed else host._stack_source_ref(spell)
    receivers = {}
    for card in sorted(host.state.cards.values(), key=lambda candidate: candidate.ref):
        if (
            card.zone != "battlefield" or card.phased_out or card.controller != actor
            or card.logical_object_id == source_identity
        ):
            continue
        types, _, _ = host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))
        if "creature" not in types:
            continue
        if fixed_cast_copy_assignment(
            host, spell, schema, card.ref, actor=spell.controller,
            acting_source=original_source, source_ref=original_ref,
        ) is None:
            continue
        assignment = fixed_cast_copy_assignment(host, spell, schema, card.ref, actor=actor, acting_source=copy_source)
        if assignment is not None:
            receivers[card.ref] = {
                "logical_object_id": card.logical_object_id, "targets": assignment.targets,
                "groups": assignment.groups, "snapshots": assignment.snapshots,
            }
    return {f"{actor}:{spell.ref}": {"copy_plans": receivers}}, {}
