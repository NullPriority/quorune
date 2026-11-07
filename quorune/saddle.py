from __future__ import annotations

"""The ordinary Saddle result and its declared-attack fact."""

from typing import Any, Mapping

from .errors import GameRuleError


SADDLE_CAPABILITY = "activation.saddle.fixed_power"
SADDLED_ATTACK_CAPABILITY = "trigger.combat.source_attacks_while_saddled"
SADDLE_OPERATION = "become_saddled"
SADDLED_ATTACK_FIELD = "attacker_saddled"
SADDLE_EFFECT_TEXT = "This permanent becomes saddled until end of turn."


def validate_saddle_ability(ability: Any) -> None:
    from .abilities import ActivatedAbility
    from .util import normalize_mana_bundle

    threshold = ability.saddle_threshold
    if type(threshold) is not int or threshold < 0:
        raise ValueError("Saddle requires a nonnegative integer threshold")
    ordinary = ActivatedAbility(ability_id=ability.ability_id, line_index=ability.line_index,
        oracle_line=ability.oracle_line, cost_text=f"Saddle {threshold}", effect_text=SADDLE_EFFECT_TEXT,
        zones=("battlefield",), mana=normalize_mana_bundle(None), sorcery_speed=True)
    descriptor = ability.to_dict()
    del descriptor["saddle_threshold"]
    if descriptor != ordinary.to_dict():
        raise ValueError("Saddle requires one fixed sorcery-speed battlefield power cost")


def is_saddled(card: Any) -> bool:
    """Read the designation for the current battlefield incarnation."""
    return (card.zone == "battlefield" and not card.phased_out
        and (card.annotations.get("until_end_of_turn") or {}).get("saddled") is True)


def apply_saddle_effect(
    host: Any, effect: Mapping[str, Any], *, actor: str, operation: str, reason: str,
) -> str | None:
    """Apply one already bound source-incarnation result."""
    ref = effect.get("card")
    if operation != SADDLE_OPERATION or type(ref) is not str or not ref:
        raise GameRuleError("Saddle requires a bound battlefield source")
    card = host._resolve_object(actor, ref, zones={"battlefield"})
    if card.phased_out:
        return None
    if is_saddled(card):
        return card.ref
    card.annotations.setdefault("until_end_of_turn", {})["saddled"] = True
    host._log(actor, "permanent.saddled", f"{card.ref} became saddled.",
        {"object": card.ref, "reason": reason}, importance=1, changed_objects=[card.object_id])
    return card.ref


def saddled_attack_condition() -> dict[str, Any]:
    return {"all": [
        {"field": "card", "op": "eq", "value": "$source.ref"},
        {"field": SADDLED_ATTACK_FIELD, "op": "eq", "value": True},
    ]}


def saddle_activation_runtime_dependencies(program: Any) -> tuple[str, ...]:
    from .rules.crew_capability_shapes import ordinary_saddle_node_capabilities
    from .rules.effect_shape_walkers import nested_effect_operations
    from .semantic_runtime.activated_abilities import activated_abilities_from_descriptors

    described = any(isinstance(handler.get("ability"), Mapping)
        and "saddle_threshold" in handler["ability"] for handler in program.handlers)
    if not described and SADDLE_OPERATION not in nested_effect_operations(program.effects):
        return ()
    abilities = activated_abilities_from_descriptors(program.handlers)
    if (program.event != "activate" or program.active_zone != "battlefield" or len(abilities) != 1
        or abilities[0].saddle_threshold is None
        or not ordinary_saddle_node_capabilities(effects=program.effects, target_schema=program.target_schema,
            mechanic_ids=("saddle",), cost_schema=program.cost_schema)
        or abilities[0].saddle_threshold != program.cost_schema["saddle"]):
        raise ValueError("Saddle requires the complete typed activation and source result")
    return (SADDLE_CAPABILITY,)


def is_closed_saddle_program(program: Any) -> bool:
    from .card_programs.admission import program_has_complete_card_program_admission

    if "saddle" not in program.coverage or not program_has_complete_card_program_admission(program):
        return False
    try:
        required = saddle_activation_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def saddled_attack_runtime_dependencies(program: Any) -> tuple[str, ...]:
    """Require the exact source and event scope for the sealed fact."""
    from .rules.event_subscriptions import FixedEventSubscriptionSet
    from .replacement.immutable import thaw_value

    def has_fact(value: Any) -> bool:
        if isinstance(value, Mapping):
            return value.get("field") == SADDLED_ATTACK_FIELD or any(has_fact(child) for child in value.values())
        return isinstance(value, (tuple, list)) and any(has_fact(child) for child in value)

    condition = program.event_condition
    if not has_fact(condition):
        return ()
    subscriptions = FixedEventSubscriptionSet.from_condition(condition)
    if subscriptions is not None:
        from types import SimpleNamespace
        return tuple(sorted({dependency
            for branch in subscriptions.subscriptions
            for dependency in saddled_attack_runtime_dependencies(
                SimpleNamespace(event=branch.event, event_condition=branch.condition)
            )}))
    if program.event != "creature.attacks" or thaw_value(condition) != saddled_attack_condition():
        raise ValueError("A saddled attack fact requires its exact self-attack condition")
    return (SADDLED_ATTACK_CAPABILITY,)
