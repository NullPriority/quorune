"""Create fixed tapped tokens from the actual graveyard destinations of Mill."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..errors import GameRuleError
from ..query_effect_amount_model import CastXAmountSpec, DECLARED_AMOUNT_CAPABILITY
from ..replacement.immutable import thaw_value
from .committed_quantity_followups import fixed_followup_capabilities, fixed_quantity_effect, quantity_followup_from_count
from .effect_shape_walkers import nested_effects


MILLED_CREATURE_TOKEN_CAPABILITY = "zone.mill.committed_creature_graveyard_tokens"


@dataclass(frozen=True, slots=True)
class MilledCreatureTokens:
    token: Mapping
    schema_version: int = 1

    def __post_init__(self):
        token = fixed_quantity_effect(self.token)
        definition = token.get("characteristics", {})
        if (type(self.schema_version) is not int or self.schema_version != 1
                or token.get("op") != "create_token" or token.get("tapped") is not True
                or not str(definition.get("type_line", "")).startswith("Token Creature — ")):
            raise ValueError("Committed creature Mill requires one ordinary tapped creature token definition")
        object.__setattr__(self, "token", token)

    def to_dict(self):
        return {"schema_version": self.schema_version, "token": thaw_value(self.token)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "token"}:
            raise ValueError("Committed creature Mill has a closed token descriptor")
        return cls(value["token"], value["schema_version"])

    def effect(self):
        return {"op": "mill", "scope": "all_active_players", "count": CastXAmountSpec().to_dict(),
                "followup": self.to_dict()}

    @property
    def dependencies(self):
        return (MILLED_CREATURE_TOKEN_CAPABILITY, DECLARED_AMOUNT_CAPABILITY, "zone.mill.fixed",
                "zone.change.destination_replacement", *fixed_followup_capabilities(self.token))

    def from_committed_results(self, host, results):
        from ..milling import MillResult

        count = 0
        for result in results:
            if not isinstance(result, MillResult):
                raise GameRuleError("Committed creature tokens require the typed Mill results")
            for outcome in result.cards:
                if outcome.destination != "graveyard":
                    continue
                card = host.state.cards.get(outcome.identity.object_id)
                if (card is None or card.zone != "graveyard" or card.logical_object_id != outcome.destination_logical_object_id
                        or card.owner != result.player or not card.is_card_object or card.face_down):
                    raise GameRuleError("A committed Mill card lost its public graveyard incarnation")
                values = host._effective_card_data(card)
                types, _, _ = host._type_parts(str(values.get("type_line") or ""))
                count += int("creature" in types)
        return quantity_followup_from_count(self.token, count)


def uses_milled_creature_tokens(effect):
    from .committed_mill_cards import uses_committed_mill_result, uses_milled_mana_damage
    from .milled_permanent_recovery import uses_milled_permanent_recovery

    return (
        isinstance(effect, Mapping) and effect.get("op") == "mill"
        and "followup" in effect and not uses_committed_mill_result(effect) and not uses_milled_permanent_recovery(effect) and not uses_milled_mana_damage(effect)
    )


def milled_creature_token_node_capabilities(effects, target_schema):
    if len(effects) != 1 or target_schema is not None or not uses_milled_creature_tokens(effects[0]):
        return ()
    effect, = effects
    try:
        result = MilledCreatureTokens.from_dict(effect["followup"])
        amount = CastXAmountSpec.from_dict(effect["count"])
        if amount != CastXAmountSpec() or dict(effect) != result.effect():
            return ()
    except (KeyError, TypeError, ValueError):
        return ()
    return result.dependencies


def milled_creature_token_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))
                   if uses_milled_creature_tokens(effect))
    if not marked and MILLED_CREATURE_TOKEN_CAPABILITY not in program.capability_dependencies:
        return ()
    required = milled_creature_token_node_capabilities(program.effects, program.target_schema)
    if (not required or marked != tuple(program.effects) or program.handlers or program.event_condition is not None
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:")):
        raise ValueError("Committed creature Mill requires the exact original declared-X spell")
    return required


def is_closed_milled_creature_token_program(program):
    try:
        required = milled_creature_token_program_dependencies(program)
    except ValueError:
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
