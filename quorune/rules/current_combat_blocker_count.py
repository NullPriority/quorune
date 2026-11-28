"""An original blocked attacker grows once according to its current blockers."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..combat import current_attacker_blocker_count
from ..continuous_effect_state import ResolutionEffectSource
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects


CURRENT_BLOCKER_COUNT_FIELD = "current_blocker_count_version"
CURRENT_BLOCKER_COUNT_CAPABILITY = "continuous.resolution.source_current_blocker_count"
CURRENT_BLOCKER_COUNT_EVENT = "creature.becomes_blocked"
_SOURCE_CONDITION = {"field": "card", "op": "eq", "value": "$source.ref"}
_DEPENDENCIES = (
    CURRENT_BLOCKER_COUNT_CAPABILITY,
    "continuous.resolution.fixed_characteristics_until_end_of_turn",
)


def current_blocker_bonus_instruction(amount):
    if type(amount) is not int or amount not in (1, 2):
        raise ValueError("Current blocker growth requires a fixed equal bonus of one or two")
    return {
        "op": "modify_stats_until_end_of_turn", "card": "$source.zone_object",
        "power": amount, "toughness": amount, CURRENT_BLOCKER_COUNT_FIELD: 1,
    }


def current_blocker_bonus_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    if len(effects) != 1 or target_schema is not None or cost_schema is not None:
        return ()
    effect = thaw_value(effects[0])
    if "cr-611-continuous-effects" not in mechanic_ids or type(effect.get(CURRENT_BLOCKER_COUNT_FIELD)) is not int:
        return ()
    try:
        expected = current_blocker_bonus_instruction(effect.get("power"))
    except ValueError:
        return ()
    if type(effect.get("toughness")) is not int or effect != expected:
        return ()
    return _DEPENDENCIES


def current_blocker_bonus_runtime_dependencies(program):
    if (
        CURRENT_BLOCKER_COUNT_CAPABILITY not in program.capability_dependencies
        and not any(CURRENT_BLOCKER_COUNT_FIELD in effect for effect in nested_effects((*program.effects, *program.handlers)))
    ):
        return ()
    required = current_blocker_bonus_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        cost_schema=program.cost_schema, mechanic_ids=program.coverage,
    )
    if (
        not required or (program.event, program.active_zone) != (CURRENT_BLOCKER_COUNT_EVENT, "battlefield")
        or thaw_value(program.event_condition) != _SOURCE_CONDITION
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage or program.handlers
    ):
        raise ValueError("Current blocker growth requires the original attacker's blocked trigger")
    return required


def resolve_current_blocker_bonus(host, effect, card, *, actor):
    source = ResolutionEffectSource.from_effect(effect)
    item = next((frame for frame in host.state.stack if frame.ref == source.stack_ref), None)
    if (
        item is None or item.kind != "triggered_ability" or item.controller != actor
        or item.context.get("event") != CURRENT_BLOCKER_COUNT_EVENT
        or item.context.get("currently_resolving") is not True
        or item.source_object_id != source.object_id
        or item.context.get("source_logical_object_id") != source.logical_object_id
        or (card.object_id, card.logical_object_id, card.ref) != (source.object_id, source.logical_object_id, source.card_ref)
        or effect.get("card") != card.ref
    ):
        raise ValueError("Current blocker growth must use its own resolving attacker")
    program = host.semantics.get(item.semantic_key)
    if program is None or not host.semantic_program_is_current_trusted(program):
        raise ValueError("Current blocker growth requires its trusted trigger")
    required = current_blocker_bonus_runtime_dependencies(program)
    expected = {**thaw_value(program.effects[0]), "card": card.ref} if required else None
    raw = {key: val for key, val in effect.items() if key not in {"_runtime_source", "_replacement_selections", "reason"}}
    if expected != raw or any(type(raw.get(key)) is not int for key in (CURRENT_BLOCKER_COUNT_FIELD, "power", "toughness")):
        raise ValueError("Current blocker growth differs from its committed instruction")
    if card.object_id not in host.state.combat.attackers:
        return 0
    return current_attacker_blocker_count(host.state, card.object_id) * raw["power"]
