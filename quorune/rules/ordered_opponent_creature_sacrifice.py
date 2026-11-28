from __future__ import annotations

from collections.abc import Mapping

from ..apnap_object_payment import ORDER_GRAVEYARD_ARRIVALS
from ..errors import GameRuleError
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects
from .graveyard_arrival_order import GRAVEYARD_ARRIVAL_ORDER_CAPABILITY


FIELD = "ordered_opponent_creature_sacrifice"
CAPABILITY = "choice.affected_player.fixed_three_opponent_ordered_sacrifice"
DEPENDENCIES = (
    CAPABILITY, "choice.affected_player.fixed_sacrifice", GRAVEYARD_ARRIVAL_ORDER_CAPABILITY,
    "trigger.placement.apnap", "zone.change.destination_replacement",
)


def ordered_opponent_sacrifice_effect(*, actor="$controller"):
    from ..compiler.affected_player_sacrifice_templates import AffectedPlayerSacrificeSubject, FixedAffectedPlayerSacrificeTemplate

    base = FixedAffectedPlayerSacrificeTemplate(
        AffectedPlayerSacrificeSubject.EACH_OPPONENT, 3, ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
    )
    return {**base.effects[0], "actor": actor, FIELD: 1}


def uses_ordered_opponent_sacrifice(effect):
    return isinstance(effect, Mapping) and effect.get("op") == "choose_cards_apnap" and FIELD in effect


def ordered_opponent_sacrifice_is_closed(effect, *, actor="$controller"):
    return (uses_ordered_opponent_sacrifice(effect) and type(effect.get(FIELD)) is int
            and type(effect.get("count")) is int and thaw_value(effect) == ordered_opponent_sacrifice_effect(actor=actor))


def ordered_opponent_sacrifice_program_dependencies(program):
    if not any(uses_ordered_opponent_sacrifice(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if (program.target_schema is not None or program.handlers or program.cost_schema is not None
            or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or len(program.effects) != 1 or not ordered_opponent_sacrifice_is_closed(program.effects[0])):
        raise ValueError("The opponent sacrifice requires its complete untargeted entry ability")
    return DEPENDENCIES


def ordered_opponent_sacrifice_commit_fields(host, item, effect):
    if not uses_ordered_opponent_sacrifice(effect):
        return {}
    if (item is None or item.kind != "triggered_ability" or item.targets
            or item.context.get("event") != "permanent.enter"
            or item.context.get("currently_resolving") is not True
            or not ordered_opponent_sacrifice_is_closed(effect, actor=item.controller)):
        raise GameRuleError("The ordered opponent sacrifice requires its original resolving entry")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not ordered_opponent_sacrifice_program_dependencies(program)):
        raise GameRuleError("The ordered opponent sacrifice requires its admitted original program")
    return {ORDER_GRAVEYARD_ARRIVALS: True}
