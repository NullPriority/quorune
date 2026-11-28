from ..replacement.immutable import thaw_value


CAPABILITY = "mana.production.fixed_resolution_turn_retained"
ATTACK_CAPABILITY = "mana.production.source_attack_turn_retained"
RETENTION = "end_of_turn"
EVENT_CONDITION = {"field": "controller", "op": "eq", "value": "$source.controller"}
PRINTED_EFFECT = "add {r}. until end of turn, you don't lose this mana as steps and phases end."
ATTACK_EVENT_CONDITION = {"field": "card", "op": "eq", "value": "$source.ref"}
ATTACK_PRINTED_EFFECT = "add {r}{r}{r}{g}{g}{g}. until end of turn, you don't lose this mana as steps and phases end."


def effect():
    return {"op": "mana", "player": "$controller", "color": "R", "amount": 1,
            "source": "$source", "retain_until": RETENTION}


def attack_effects():
    return tuple({**effect(), "color": color, "amount": 3} for color in ("R", "G"))


def program_dependencies(program):
    if (getattr(program, "active_zone", None) != "battlefield" or program.target_schema is not None
            or program.cost_schema is not None or program.handlers):
        raise ValueError("Turn-retained mana requires its complete untargeted battlefield trigger")
    scope = (program.event, thaw_value(program.event_condition), thaw_value(program.effects))
    if scope == ("spell.cast", EVENT_CONDITION, [effect()]):
        return (CAPABILITY, "mana.production.fixed_resolution")
    if scope == ("creature.attacks", ATTACK_EVENT_CONDITION, list(attack_effects())) and program.trigger_limit is None:
        return (ATTACK_CAPABILITY, "mana.production.fixed_resolution")
    raise ValueError("Turn-retained mana requires an exact supported event and bundle")


def validate_resolution(host, instruction, *, actor):
    from ..continuous_effect_state import ResolutionEffectSource

    source = ResolutionEffectSource.from_effect(instruction)
    item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
    if (item is None or item.kind != "triggered_ability" or item.controller != actor
            or item.context.get("currently_resolving") is not True):
        raise ValueError("Turn-retained mana lost its original resolving cast trigger")
    program = host.semantics.get(item.semantic_key)
    if program is None:
        raise ValueError("Turn-retained mana requires its compiled trigger")
    program_dependencies(program)
    if program.event == "creature.attacks":
        source_id = item.source_object_id
        source_present = source_id in host.state.cards
        if (item.context.get("event") != "creature.attacks" or not source_id
                or source.object_id != (source_id if source_present else None)
                or not host.semantic_program_is_current_trusted(program)):
            raise ValueError("Turn-retained attack mana lost its original trusted attacking source")
        allowed = (("R", 3, actor, None), ("G", 3, actor, None))
    else:
        allowed = (("R", 1, actor, None),)
    if (instruction.get("color"), instruction.get("amount"), instruction.get("player"),
            instruction.get("spend_restriction")) not in allowed:
        raise ValueError("Turn-retained mana changed its exact controller and output")


FIGHT_EXCESS_RED_FIELD = "fight_excess_red_version"
TURN_POOL_COLOR_FIELD = "turn_pool_color_retention_version"
FIGHT_EXCESS_RED_CAPABILITY = "mana.production.fight_excess_red"
TURN_POOL_COLOR_CAPABILITY = "mana.pool.turn_color_retention"


def turn_pool_red_instruction():
    return {"op": "mana", "player": "$controller", "color": "R", "amount": 0,
            TURN_POOL_COLOR_FIELD: 1}


def _exact_turn_pool_red_instruction(instruction, *, player):
    expected = {**turn_pool_red_instruction(), "player": player}
    payload = {key: value for key, value in instruction.items() if key != "_runtime_source"}
    return (payload == expected and type(payload.get(TURN_POOL_COLOR_FIELD)) is int
            and type(payload.get("amount")) is int)


def uses_fight_excess_red(effects):
    return any(FIGHT_EXCESS_RED_FIELD in effect or TURN_POOL_COLOR_FIELD in effect for effect in effects)


def fight_excess_red_node_dependencies(effects, target_schema, mechanics):
    from ..replacement.immutable import FrozenMap
    from .creature_power_damage_capability_shapes import fixed_creature_power_damage_node_capabilities

    if len(effects) != 2 or not _exact_turn_pool_red_instruction(effects[1], player="$controller"):
        raise ValueError("Fight excess mana requires the original two ordered spell instructions")
    fight = dict(effects[0])
    version = fight.pop(FIGHT_EXCESS_RED_FIELD, None)
    target = thaw_value(FrozenMap(target_schema)) if target_schema is not None else None
    required = fixed_creature_power_damage_node_capabilities(
        effects=(fight,), target_schema=target, mechanic_ids=mechanics,
    )
    groups = target.get("groups", ()) if isinstance(target, dict) else ()
    if (type(version) is not int or version != 1 or fight.get("kind") != "fight" or not required
            or len(groups) != 2 or [group.get("controller_relation") for group in groups] != ["you", "opponent"]
            or any(group.get("count") != 1 for group in groups)):
        raise ValueError("Fight excess mana lost its controlled-versus-opponent fight")
    return (*required, FIGHT_EXCESS_RED_CAPABILITY, TURN_POOL_COLOR_CAPABILITY, "mana.production.fixed_resolution")


def fight_excess_red_program_dependencies(program):
    if not uses_fight_excess_red(program.effects):
        return ()
    if (program.event != "resolve" or program.active_zone != "stack"
            or program.handlers or program.cost_schema is not None):
        raise ValueError("Fight excess mana requires its original resolving spell")
    return fight_excess_red_node_dependencies(program.effects, program.target_schema, program.coverage)


def apply_turn_pool_red(host, effect, *, actor):
    from ..continuous_effect_state import ResolutionEffectSource
    from ..mana_provenance import TURN_POOL_COLOR_STAT

    marker = ResolutionEffectSource.from_effect(effect)
    item = next((item for item in host.state.stack if item.ref == marker.stack_ref), None)
    program = host.semantics.get(item.semantic_key) if item is not None else None
    if (item is None or item.kind not in {"spell", "spell_copy"} or item.controller != actor
            or item.context.get("currently_resolving") is not True or program is None
            or not host.semantic_program_is_current_trusted(program)
            or not fight_excess_red_program_dependencies(program)
            or not _exact_turn_pool_red_instruction(effect, player=actor)):
        raise ValueError("Turn red retention requires its original resolving spell")
    host.state.players[actor].stats[TURN_POOL_COLOR_STAT] = host.state.turn_sequence
