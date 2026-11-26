from ..replacement.immutable import thaw_value


CAPABILITY = "mana.production.fixed_resolution_turn_retained"
RETENTION = "end_of_turn"
EVENT_CONDITION = {"field": "controller", "op": "eq", "value": "$source.controller"}
PRINTED_EFFECT = "add {r}. until end of turn, you don't lose this mana as steps and phases end."


def effect():
    return {"op": "mana", "player": "$controller", "color": "R", "amount": 1,
            "source": "$source", "retain_until": RETENTION}


def program_dependencies(program):
    if (program.event != "spell.cast" or program.active_zone != "battlefield"
            or thaw_value(program.event_condition) != EVENT_CONDITION or program.target_schema is not None
            or program.cost_schema is not None or program.handlers
            or thaw_value(program.effects) != [effect()]):
        raise ValueError("Turn-retained red mana requires its exact own-cast trigger")
    return (CAPABILITY, "mana.production.fixed_resolution")


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
    if (instruction.get("color"), instruction.get("amount"), instruction.get("player"),
            instruction.get("spend_restriction")) != ("R", 1, actor, None):
        raise ValueError("Turn-retained mana changed its exact controller and red output")
