"""Typed Gift extra turn and its separate promised controller protection."""

from collections.abc import Mapping

from ..fixed_token_production import TOKEN_LITERAL_NAME_VERSION_FIELD
from ..gift_card_model import GIFT_CARD_PROMISE_FIELD, GIFT_EXTRA_TURN_COST_FIELD, GiftCardPromise
from ..phasing_model import (
    CONTROLLED_PERMANENT_PHASE_OUT_CAPABILITY, PHASE_OUT_CAPABILITY, PHASING_CAPABILITY,
    controlled_permanent_set_phase_out_effect,
)
from ..replacement.immutable import thaw_value
from .extra_turn_shapes import FIXED_EXTRA_TURN_CAPABILITY
from .next_personal_turn_protection import NEXT_PERSONAL_TURN_PROTECTION_CAPABILITY, next_personal_turn_protection_effect
from .spell_self_exile import SELF_EXILE


GIFT_EXTRA_TURN_CAPABILITY = "casting.additional_cost.gift_extra_turn_protection_spell"
GIFT_EXTRA_TURN_TEMPLATE = SELF_EXILE.template + "+gift-extra-turn-token-and-controller-protection-v1"
GIFT_EXTRA_TURN_PRICE = {"schema_version": 1}
DEPENDENCIES = (
    GIFT_EXTRA_TURN_CAPABILITY, FIXED_EXTRA_TURN_CAPABILITY, "token.creation.fixed_definition", "combat.block.flying",
    NEXT_PERSONAL_TURN_PROTECTION_CAPABILITY, "life.change.effect", CONTROLLED_PERMANENT_PHASE_OUT_CAPABILITY,
    PHASE_OUT_CAPABILITY, PHASING_CAPABILITY, SELF_EXILE.capability,
)


def flying_bird_creation_effect():
    return {
        "op": "create_token",
        "controller": "$controller",
        "name": "Bird Token",
        "quantity": 4,
        "characteristics": {
            "type_line": "Token Creature — Bird", TOKEN_LITERAL_NAME_VERSION_FIELD: 1,
            "colors": ["U"], "power": "2", "toughness": "2", "keywords": ["Flying"],
        },
    }


def gift_extra_turn_protection_effects():
    return (
        flying_bird_creation_effect(), controlled_permanent_set_phase_out_effect(), next_personal_turn_protection_effect(),
    )


def gift_extra_turn_runtime_dependencies(program):
    cost, provenance = program.cost_schema, program.provenance or {}
    has_cost = isinstance(cost, Mapping) and GIFT_EXTRA_TURN_COST_FIELD in cost
    has_template = provenance.get("template_id") == GIFT_EXTRA_TURN_TEMPLATE
    if not (has_cost or has_template):
        return ()
    price = cost.get(GIFT_EXTRA_TURN_COST_FIELD) if has_cost else None
    if (not has_template or not has_cost or set(cost) != {GIFT_EXTRA_TURN_COST_FIELD}
            or not isinstance(price, Mapping) or dict(price) != GIFT_EXTRA_TURN_PRICE
            or type(price["schema_version"]) is not int
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not getattr(program, "ability_id", "spell:component").startswith("spell:")
            or getattr(program, "destination", SELF_EXILE.destination) != SELF_EXILE.destination
            or SELF_EXILE.mechanic not in program.coverage
            or program.event_condition is not None or program.target_schema is not None or program.handlers
            or tuple(thaw_value(program.effects)) != gift_extra_turn_protection_effects()):
        raise ValueError("Gift an extra turn requires its complete resolving token, protection, phasing, and exile instructions")
    return DEPENDENCIES


def prepare_gift_extra_turn_resolution(host, item, effects):
    if item.kind not in {"spell", "spell_copy"} or tuple(effects) != gift_extra_turn_protection_effects():
        raise ValueError("The resolving Gift an extra turn spell lost its validated instructions")
    promise = GiftCardPromise.from_dict(item.context.get(GIFT_CARD_PROMISE_FIELD))
    if promise.recipient is None:
        return [effects[0]]
    if promise.recipient not in host.state.players:
        raise ValueError("The resolving Gift an extra turn spell lost its chosen player")
    if promise.recipient not in host.active_seats:
        return effects
    return [{"op": "extra_turn", "player": promise.recipient, "source": "$stack"}, *effects]
