from dataclasses import dataclass
from typing import Any


CREATURE_ABILITY_ACTIVATED = "ability.creature.activated"
CREATURE_ABILITY_ACTIVATION_CAPABILITY = "trigger.event.creature_ability_activation"


def creature_ability_activator_condition() -> dict[str, str]:
    return {"field": "player", "op": "eq", "value": "$source.controller"}


@dataclass(frozen=True, slots=True)
class CreatureAbilityActivation:
    """Pointer to the original public source and the authenticated activator."""

    actor: str
    source_ref: str
    source_incarnation: str

    def __post_init__(self) -> None:
        if any(type(part) is not str or not part for part in (
            self.actor, self.source_ref, self.source_incarnation,
        )):
            raise ValueError("A creature activation requires an actor and its original source incarnation")

    def to_context(self) -> dict[str, str]:
        return {"player": self.actor, "card": self.source_ref, "card_object_identity": self.source_incarnation}


def capture_creature_ability_activation(host: Any, actor: str, source: Any) -> CreatureAbilityActivation | None:
    """Capture the acting source before an activation cost can change zones."""
    from .event_subscriptions import event_has_capability_subscriber

    if not event_has_capability_subscriber(host, CREATURE_ABILITY_ACTIVATED, CREATURE_ABILITY_ACTIVATION_CAPABILITY):
        return None
    if source.zone != "battlefield" or source.phased_out:
        return None
    types, _, _ = host._type_parts(str(host._effective_card_data(source).get("type_line") or ""))
    if "creature" not in types:
        return None
    return CreatureAbilityActivation(actor, source.ref, source.logical_object_id)


def publish_creature_ability_activation(host: Any, activation: CreatureAbilityActivation | None) -> bool:
    """Discover ordinary triggers at successful activation, deferring their placement."""
    if activation is None:
        return False
    refs = host._dispatch_semantic_event(
        CREATURE_ABILITY_ACTIVATED, activation.to_context(),
        sources=host._semantic_event_sources(zones={"battlefield"}),
    )
    return bool(refs)


def creature_ability_activation_runtime_dependencies(program: Any, subscriptions: Any) -> tuple[str, ...]:
    from .trigger_limits import TriggerLimitSpec

    nested = subscriptions is not None and subscriptions.subscription_for(CREATURE_ABILITY_ACTIVATED) is not None
    if (program.event != CREATURE_ABILITY_ACTIVATED and not nested
            and CREATURE_ABILITY_ACTIVATION_CAPABILITY not in program.capability_dependencies):
        return ()
    if (program.event != CREATURE_ABILITY_ACTIVATED or program.active_zone != "battlefield"
            or program.event_condition != creature_ability_activator_condition()
            or program.target_schema is not None or program.handlers or program.cost_schema is not None
            or program.trigger_limit != TriggerLimitSpec().to_dict()
            or tuple(program.effects) != ({"op": "draw", "player": "$controller", "count": 1, "private": True},)
            or type(program.effects[0].get("count")) is not int or program.effects[0].get("private") is not True):
        raise ValueError("A creature activation observer requires its single private controller draw and turn limit")
    return (CREATURE_ABILITY_ACTIVATION_CAPABILITY,)
