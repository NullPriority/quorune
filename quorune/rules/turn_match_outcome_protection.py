"""Controller outcome protection and damage-result scope through cleanup."""

from collections.abc import Mapping

from ..turn_match_outcome_model import TURN_OUTCOME_FIELD


TURN_OUTCOME_CAPABILITY = "rule.player.turn_match_outcome_protection"
TURN_OUTCOME_TEMPLATE = "damage-turn-controller-outcome-protection-v1"


def turn_match_outcome_protection() -> dict:
    return {"op": "damage", "source": "$stack", "player": "$controller", TURN_OUTCOME_FIELD: 1}


def validate_turn_match_outcome_protection(effect: Mapping, *, actor="$controller", source="$stack") -> None:
    expected = {**turn_match_outcome_protection(), "player": actor, "source": source}
    if not isinstance(effect, Mapping) or type(effect.get(TURN_OUTCOME_FIELD)) is not int or effect != expected:
        raise ValueError("Turn outcome protection requires the resolving controller and its sealed source")


def turn_match_outcome_node_capabilities(effects, target_schema) -> tuple[str, ...]:
    if target_schema is not None or len(effects) != 1:
        return ()
    try:
        validate_turn_match_outcome_protection(effects[0])
    except ValueError:
        return ()
    from .damage_capability_shapes import DAMAGE_RESULT_REPLACEMENT_CAPABILITY

    return (TURN_OUTCOME_CAPABILITY, DAMAGE_RESULT_REPLACEMENT_CAPABILITY)


def turn_match_outcome_runtime_dependencies(program) -> tuple[str, ...]:
    from .effect_shape_walkers import nested_effects

    effects = (*program.effects, *program.handlers)
    marked = program.provenance.get("template_id") == TURN_OUTCOME_TEMPLATE
    if not marked and not any(TURN_OUTCOME_FIELD in effect for effect in nested_effects(effects)):
        return ()
    required = turn_match_outcome_node_capabilities(program.effects, program.target_schema)
    if (not marked or (program.event, program.active_zone) != ("resolve", "stack") or program.handlers
            or program.event_condition is not None or not required):
        raise ValueError("Turn outcome protection requires one exact resolving spell effect")
    return required
