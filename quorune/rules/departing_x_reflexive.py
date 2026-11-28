"""Pay the admitted variable cost and queue one original source damage trigger."""
from dataclasses import dataclass

from ..errors import GameRuleError
from ..reflexive_trigger_model import DEPARTURE_X_PAYMENT_OPERATION
from ..replacement.immutable import FrozenMap

OPERATION = DEPARTURE_X_PAYMENT_OPERATION
MECHANIC = "departure-x-payment-reflexive-player-damage"
CAPABILITY = "trigger.reflexive.departure_generic_x_damage"
RED_DEATH_VERSION = 2
RED_DEATH_MECHANIC = "death-red-payment-reflexive-damage"
RED_DEATH_CAPABILITY = "trigger.reflexive.death_variable_red_damage"
DEPENDENCIES = (CAPABILITY, "effect.choice.optional_fixed_mana_payment", "damage.amount.positive",
                "damage.result.player_life", "damage.source.stack_current_or_lki", "target.revalidate_resolution",
                "trigger.reflexive.fixed_action_result", "trigger.event.normalized_zone_change", "trigger.placement.apnap")


RED_DEATH_DEPENDENCIES = (*DEPENDENCIES, RED_DEATH_CAPABILITY, "damage.result.multitype_permanent",
                         "target.public.player_or_damageable_permanent")


def instruction(version=1):
    if type(version) is not int or version not in (1, RED_DEATH_VERSION):
        raise ValueError("Unknown variable departure payment version")
    return {"op": OPERATION, "schema_version": version, "player": "$controller"}


def payment_symbol(version):
    return "R" if version == RED_DEATH_VERSION else "GENERIC"


def node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or target_schema is not None:
        return ()
    version = effects[0].get("schema_version")
    if (type(version) is not int or version not in (1, RED_DEATH_VERSION)
            or FrozenMap(effects[0]) != FrozenMap(instruction(version))):
        return ()
    if version == RED_DEATH_VERSION:
        return RED_DEATH_DEPENDENCIES if RED_DEATH_MECHANIC in mechanic_ids else ()
    return DEPENDENCIES if MECHANIC in mechanic_ids else ()


def program_dependencies(program):
    if (not any(effect.get("op") == OPERATION for effect in program.effects)
            and RED_DEATH_CAPABILITY not in program.capability_dependencies):
        return ()
    owned = node_capabilities(effects=program.effects, target_schema=program.target_schema,
                              mechanic_ids=program.coverage)
    if not owned or program.event_condition is not None or program.cost_schema is not None or program.handlers:
        raise ValueError("Paid damage requires its original nontargeted source trigger")
    red_death = program.effects[0]["schema_version"] == RED_DEATH_VERSION
    expected = "creature.dies.self" if red_death else "permanent.leave.self"
    if ((program.event, program.active_zone) != (expected, "battlefield")
            or red_death and program.trigger_limit is not None):
        raise ValueError("Paid damage requires its original leave or death event")
    return owned


def is_closed_program(program):
    result = program_dependencies(program)
    return bool(result) and set(result) <= set(program.capability_dependencies)


def _trusted_parent(host, item):
    if item is None or item.kind not in {"triggered_ability","triggered_ability_copy"}:
        raise GameRuleError("The departing source's ordinary trigger is unavailable")
    current = host.semantics.get(item.semantic_key)
    if (not item.context.get("currently_resolving") or current is None or not is_closed_program(current)
            or not host.semantic_program_is_current_trusted(current)):
        raise GameRuleError("The departing source's exact X-payment program is unavailable")
    return item.controller, current.effects[0]["schema_version"]


def bind_choice(host, item, effect):
    if effect.get("op") != OPERATION:
        return effect
    payer, version = _trusted_parent(host, item)
    original = {**instruction(version), "player": payer}
    if "stage" not in effect:
        if FrozenMap(effect) != FrozenMap(original) or type(effect.get("schema_version")) is not int:
            raise GameRuleError("The departure payment belongs to its original resolving controller and instruction")
        return effect
    if (FrozenMap(effect) != FrozenMap({**original, "stage": "amount"})
            or type(effect.get("schema_version")) is not int):
        raise ValueError("The departure payment quote has a closed stage")
    cost_symbol = payment_symbol(version)
    minimum, upper = 0, 1
    while host._cost_is_affordable(payer, {cost_symbol: upper}):
        minimum, upper = upper, upper * 2
    while minimum + 1 < upper:
        middle = (minimum + upper) // 2
        if host._cost_is_affordable(payer, {cost_symbol: middle}):
            minimum = middle
        else:
            upper = middle
    return {**effect, "maximum": minimum}


@dataclass(frozen=True, slots=True)
class PaidDepartureDamageIntent:
    controller: str
    parent_stack_ref: str
    amount: int

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.controller,self.parent_stack_ref))
                or type(self.amount) is not int or self.amount < 0):
            raise ValueError("Paid departure damage requires a resolving frame and nonnegative announced X")


def queue_paid_damage(host, intent):
    from ..reflexive_trigger_model import BOUND_REFLEXIVE_PROGRAM_VERSION, ReflexiveTriggerProgram
    from .reflexive_triggers import _bound_reflexive_context, _place_reflexive_trigger

    item = next((candidate for candidate in host.state.stack if candidate.ref == intent.parent_stack_ref), None)
    payer, version = _trusted_parent(host, item)
    if payer != intent.controller:
        raise GameRuleError("The reflexive consequence lost its original payer")
    schema = {"zones": ["player"], "categories": ["player"], "count": 1}
    if version == RED_DEATH_VERSION:
        schema = {"zones": ["player", "battlefield"], "categories": ["player", "permanent"],
                  "predicate": "damageable", "count": 1}
    consequence = ReflexiveTriggerProgram(
        ({"op":"damage","source":"$source","target":"$target.0","amount":intent.amount},),
        schema, ("cr-120-damage","cr-115-targets"),
        schema_version=BOUND_REFLEXIVE_PROGRAM_VERSION,
    )
    source_id = item.source_object_id
    source_context = _bound_reflexive_context(item, consequence, source_id)
    return _place_reflexive_trigger(host, item, consequence, source_id=source_id, source_context=source_context)
