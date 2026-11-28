"""A resolving spell protects its selected opponent and phases their nonlands."""

from collections.abc import Mapping

from ..phasing_model import (
    CONTROLLED_PERMANENT_PHASE_OUT_CAPABILITY, PHASE_OUT_CAPABILITY, PHASE_OUT_OPERATION,
    PHASING_CAPABILITY, TARGET_PLAYER_NONLAND_PHASE_OUT_SUBJECT, TARGET_PLAYER_NONLAND_PHASE_OUT_VERSION,
    target_player_nonland_phase_out_effect, validate_phase_out_effect,
)
from ..replacement.immutable import FrozenMap
from .next_personal_turn_protection import PROTECTION_OPERATION, next_personal_turn_protection_effect


CAPABILITY = "protection.player.opponent_until_next_turn_nonland_phasing"
MECHANIC = "fixed-targeted-opponent-protection-and-nonland-phasing"
TEMPLATE = "fixed-targeted-opponent-protection-and-nonland-phasing-v1"
PROTECTION_VERSION = 5
DEPENDENCIES = (
    CAPABILITY, "life.change.effect", "target.revalidate_resolution",
    CONTROLLED_PERMANENT_PHASE_OUT_CAPABILITY, PHASE_OUT_CAPABILITY, PHASING_CAPABILITY,
)


def opponent_target_schema():
    return {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "opponent"}


def protection_effect():
    return {**next_personal_turn_protection_effect(), "schema_version": PROTECTION_VERSION, "player": "$target.0"}


def effects():
    return protection_effect(), target_player_nonland_phase_out_effect()


def node_capabilities(*, effects: tuple, target_schema: Mapping | None, mechanics) -> tuple[str, ...]:
    if (MECHANIC not in mechanics or not isinstance(target_schema, Mapping)
            or type(target_schema.get("count")) is not int
            or FrozenMap(target_schema) != FrozenMap(opponent_target_schema()) or len(effects) != 2):
        return ()
    protection, phasing = effects
    if (type(protection.get("schema_version")) is not int or protection.get("preserve_life_total") is not True
            or protection != protection_effect()):
        return ()
    try:
        subject = validate_phase_out_effect(phasing)
    except ValueError:
        return ()
    return DEPENDENCIES if subject == TARGET_PLAYER_NONLAND_PHASE_OUT_SUBJECT else ()


def uses_targeted_opponent_protection(effect):
    return (
        (effect.get("op") == PROTECTION_OPERATION and effect.get("schema_version") == PROTECTION_VERSION)
        or (effect.get("op") == PHASE_OUT_OPERATION and (
            effect.get("subject") == TARGET_PLAYER_NONLAND_PHASE_OUT_SUBJECT
            or effect.get("schema_version") == TARGET_PLAYER_NONLAND_PHASE_OUT_VERSION
        ))
    )


def program_dependencies(program):
    required = node_capabilities(effects=program.effects, target_schema=program.target_schema, mechanics=program.coverage)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:") or program.handlers
            or program.cost_schema is not None or program.event_condition is not None):
        raise ValueError("Next-turn opponent protection requires the complete targeted resolving spell")
    return required


def validate_resolving_opponent_protection(host, effect, *, actor):
    from ..continuous_effect_state import ResolutionEffectSource

    source = ResolutionEffectSource.from_effect(effect)
    player = effect.get("player")
    expected = protection_effect() if effect.get("op") == PROTECTION_OPERATION else target_player_nonland_phase_out_effect()
    instruction = {key: value for key, value in effect.items() if key not in {"_runtime_source", "reason"}}
    if (type(player) is not str or player == actor or player not in host.active_seats
            or type(effect.get("schema_version")) is not int or instruction.get("source") != source.stack_ref
            or instruction != {**expected, "player": player, "source": source.stack_ref}
            or (effect.get("op") == PROTECTION_OPERATION and effect.get("preserve_life_total") is not True)):
        raise ValueError("Next-turn opponent protection requires its original selected opponent and stack reference")
    item = next((row for row in host.state.stack if row.ref == source.stack_ref), None)
    if (item is None or item.controller != actor or item.kind not in {"spell", "spell_copy"}
            or item.context.get("currently_resolving") is not True or item.context.get("targets_revalidated") is not True
            or tuple(item.targets) != (player,)):
        raise ValueError("Next-turn opponent protection requires its resolving spell and revalidated opponent")
    program = host.semantics.get(item.semantic_key)
    if program is None or not host.semantic_program_is_current_trusted(program):
        raise ValueError("Next-turn opponent protection requires its admitted resolving spell")
    program_dependencies(program)
    return player
