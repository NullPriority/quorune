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
