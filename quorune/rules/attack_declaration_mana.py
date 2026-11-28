"""One combat-retained red bundle for a completed player attack declaration."""

from ..attack_transition_model import AttackTransitionEvent
from ..replacement.immutable import thaw_value


EVENT = "player.attacks"
CAPABILITY = "mana.production.player_attack_combat_retained"
TEMPLATE = "fixed-player-attack-combat-retained-mana-v1"
VARIANT = "any_player_attack_declaration_combat_mana"
DEPENDENCIES = (
    CAPABILITY, "mana.production.fixed_resolution", "trigger.event.normalized_public_action",
    "trigger.effect.fixed_event", "trigger.placement.apnap",
)


def effect(amount):
    return {"op": "mana", "player": "$controller", "color": "R", "amount": amount,
            "retain_until": "end_of_combat", "source": "$source"}


def closed_effect(effects):
    if len(effects) != 1:
        return False
    amount = effects[0].get("amount")
    return (type(amount) is int and 1 <= amount <= 10
            and thaw_value(effects[0]) == effect(amount))


def program_dependencies(program):
    if program.event != EVENT:
        return ()
    if (program.active_zone != "battlefield" or program.target_schema is not None
            or program.cost_schema is not None or program.event_condition is not None
            or program.handlers or program.trigger_limit is not None or not closed_effect(program.effects)):
        raise ValueError("Player-attack mana needs its complete untargeted declaration trigger")
    return DEPENDENCIES


def dispatch_player_attack_declaration(host, event, *, receivers, trigger_batch):
    from .event_subscriptions import event_has_capability_subscriber

    if not event.assignments or not event_has_capability_subscriber(host, EVENT, CAPABILITY):
        return ()
    return host._dispatch_semantic_event(
        EVENT, {"event_id": event.transition_id, "player": event.active_player,
                "attack_transition": event.to_dict()},
        sources=receivers, trigger_batch=trigger_batch,
    )


def validate_resolution(host, instruction, *, actor):
    from ..continuous_effect_state import ResolutionEffectSource

    item = host.state.stack[-1] if host.state.stack else None
    program = host.semantics.get(item.semantic_key) if item is not None else None
    if program is None or program.event != EVENT:
        return
    source = ResolutionEffectSource.from_effect(instruction)
    if (item.ref != source.stack_ref or item.kind != "triggered_ability" or item.controller != actor
            or item.context.get("currently_resolving") is not True
            or item.context.get("event") != EVENT or not host.semantic_program_is_current_trusted(program)):
        raise ValueError("Combat-retained declaration mana lost its resolving trigger")
    program_dependencies(program)
    event = AttackTransitionEvent.from_dict(item.context.get("attack_transition"))
    if not event.assignments or (event.transition_id, event.active_player) != (
            item.context.get("event_id"), item.context.get("player")):
        raise ValueError("Combat-retained mana requires a genuine nonempty attack declaration")
    if (instruction.get("player"), instruction.get("color"), instruction.get("amount"),
            instruction.get("spend_restriction")) != (actor, "R", program.effects[0]["amount"], None):
        raise ValueError("Combat-retained declaration mana changed its controller or bundle")
