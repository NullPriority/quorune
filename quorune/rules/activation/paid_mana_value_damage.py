from __future__ import annotations

"""Bind a fixed bonus and one paid permanent's mana value to ordinary damage."""

from collections.abc import Mapping
from dataclasses import dataclass

from ...activation_condition_model import ActivationCondition, ActivationConditionKind
from ...activation_cost_choice import CostChoice
from ...boast import BOAST_CAPABILITY
from ...errors import GameRuleError
from ...object_predicate import ObjectQuerySpec
from ...public_quantity_reductions import public_mana_value
from ...replacement.immutable import FrozenMap, thaw_value
from ...scalar_effect_amount_model import (
    PAID_ACTIVATION_DAMAGE_MANA_VALUE_CAPABILITY, PAID_ACTIVATION_DAMAGE_MANA_VALUE_VERSION,
    SCALAR_AMOUNT_CAPABILITY, SCALAR_AMOUNT_MECHANIC,
    PaidSacrificeManaValueSnapshot, ScalarAmountOrigin, ScalarEffectAmountSpec, scalar_amount_specs,
)
from ...util import stable_json
from ..activation_costs import activation_choice_candidates
from ..damage_capability_shapes import _FIXED_DAMAGE_TARGET_SCHEMAS, fixed_damage_node_capabilities


PAID_DAMAGE_MANA_VALUE_MECHANIC = "paid-activation-damage-mana-value"
PAID_DAMAGE_MANA_VALUE_CONTEXT = "paid_activation_damage_mana_value"
PAID_DAMAGE_MANA_VALUE_TEMPLATE = "boast-paid-permanent-mana-value-damage-v1"
PAID_DAMAGE_MANA_VALUE_MECHANICS = (
    "cr-120-damage", "cr-115-targets", SCALAR_AMOUNT_MECHANIC, PAID_DAMAGE_MANA_VALUE_MECHANIC,
)
PRINTED_PAID_DAMAGE_RESULT = (
    "This creature deals damage equal to 2 plus the sacrificed permanent's mana value to any target."
)
_CHOICE = CostChoice(
    "sacrifice_one", 1, "battlefield", another=True,
    predicate=FrozenMap(ObjectQuerySpec(
        zones=("battlefield",), controller="$actor", types_any=("artifact", "creature"), known_to_actor=True,
    ).to_dict()),
)


def paid_damage_mana_value_price_is_closed(cost_schema) -> bool:
    expected = {
        "text": "Sacrifice another creature or artifact",
        "mana": dict.fromkeys((*"WUBRGC", "GENERIC"), 0),
        "complex_symbols": [], "uncompiled_costs": [],
        "tap_source": False, "untap_source": False, "discard_source": False,
        "exile_source": False, "sacrifice_source": False,
        "life_payment": 0, "energy_payment": 0, "loyalty_delta": None,
        "source_counter_removal_cost": None, "choices": [_CHOICE.compact()],
        "activation_limit": "once_per_turn", "boast": True,
    }
    return isinstance(cost_schema, Mapping) and stable_json(thaw_value(cost_schema)) == stable_json(expected)


def paid_damage_mana_value_target_schema() -> dict:
    return thaw_value(_FIXED_DAMAGE_TARGET_SCHEMAS["any_target"])


def paid_damage_mana_value_effect() -> dict:
    amount = ScalarEffectAmountSpec(
        ScalarAmountOrigin.PAID_ACTIVATION_DAMAGE_MANA_VALUE,
        characteristic="mana_value", schema_version=PAID_ACTIVATION_DAMAGE_MANA_VALUE_VERSION, offset=2,
    )
    return {"op": "damage", "source": "$source", "target": "$target.0", "amount": amount.to_dict()}


def paid_damage_mana_value_node_capabilities(*, effects, target_schema, active_zone, cost_schema, mechanics):
    if (
        active_zone != "battlefield" or not set(PAID_DAMAGE_MANA_VALUE_MECHANICS) <= set(mechanics)
        or not paid_damage_mana_value_price_is_closed(cost_schema)
        or stable_json(thaw_value(target_schema)) != stable_json(paid_damage_mana_value_target_schema())
        or stable_json(thaw_value(effects)) != stable_json([paid_damage_mana_value_effect()])
    ):
        return ()
    damage = fixed_damage_node_capabilities(
        effects=({**paid_damage_mana_value_effect(), "amount": 2},),
        target_schema=target_schema, mechanic_ids=mechanics,
    )
    if not damage:
        return ()
    return tuple(dict.fromkeys((
        *damage, SCALAR_AMOUNT_CAPABILITY, PAID_ACTIVATION_DAMAGE_MANA_VALUE_CAPABILITY,
        "activation.selected_zone_change.fixed", BOAST_CAPABILITY, "activation.usage.once_per_turn",
        "target.revalidate_resolution",
    )))


def _ability_matches(ability, program) -> bool:
    from ...compiler.activated_costs import activated_ability_cost

    return (
        ability.builtin_semantic_key == program.key and ability.boast is True
        and ability.zones == ("battlefield",) and not ability.mana_ability and not ability.sorcery_speed
        and ability.activation_conditions == (ActivationCondition(ActivationConditionKind.SOURCE_ATTACKED_THIS_TURN),)
        and ability.choices == (_CHOICE,)
        and thaw_value(ability.target_schema) == thaw_value(program.target_schema)
        and paid_damage_mana_value_price_is_closed(activated_ability_cost(ability))
    )


def paid_damage_mana_value_runtime_dependencies(program):
    marker = PAID_DAMAGE_MANA_VALUE_MECHANIC in program.coverage
    values = (program.effects, program.handlers, program.target_schema, program.cost_schema)
    specs = tuple(
        spec for spec in scalar_amount_specs(values)
        if spec.origin is ScalarAmountOrigin.PAID_ACTIVATION_DAMAGE_MANA_VALUE
    )
    if not marker and not specs:
        return ()
    required = paid_damage_mana_value_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, active_zone=program.active_zone,
        cost_schema=program.cost_schema, mechanics=program.coverage,
    )
    if (
        not required or program.event != "activate" or program.event_condition is not None
        or program.provenance.get("granted_only") is True or len(program.handlers) != 1
        or len(specs) != 1 or scalar_amount_specs(program.effects) != specs
    ):
        raise ValueError("Paid mana-value damage requires the exact battlefield Boast and sacrifice")
    from ...semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    if not _ability_matches(ability, program):
        raise ValueError("Paid mana-value damage changed its typed price, usage or target")
    return required


@dataclass(frozen=True, slots=True)
class PaidDamageManaValueFact:
    semantic_key: str
    source_logical_object_id: str
    paid_object_id: str
    paid_ref: str
    snapshot: PaidSacrificeManaValueSnapshot
    schema_version: int = 1

    def __post_init__(self):
        if (
            type(self.schema_version) is not int or self.schema_version != 1
            or any(type(value) is not str or not value for value in (
                self.semantic_key, self.source_logical_object_id, self.paid_object_id, self.paid_ref,
            ))
            or not isinstance(self.snapshot, PaidSacrificeManaValueSnapshot)
            or not self.snapshot.logical_object_id.startswith(self.paid_object_id + "@")
            or self.snapshot.logical_object_id == self.source_logical_object_id
        ):
            raise ValueError("Paid mana-value damage requires the exact source and sacrificed incarnation")

    def to_dict(self):
        return {
            "schema_version": self.schema_version, "semantic_key": self.semantic_key,
            "source_logical_object_id": self.source_logical_object_id,
            "paid_object_id": self.paid_object_id, "paid_ref": self.paid_ref, "snapshot": self.snapshot.to_dict(),
        }

    @classmethod
    def from_dict(cls, value):
        fields = {"schema_version", "semantic_key", "source_logical_object_id", "paid_object_id", "paid_ref", "snapshot"}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ValueError("Paid mana-value damage fields are incomplete or unknown")
        return cls(**{**value, "snapshot": PaidSacrificeManaValueSnapshot.from_dict(value["snapshot"])})


def capture_paid_damage_mana_value(host, proposal, source, ability, response, program):
    required = paid_damage_mana_value_runtime_dependencies(program)
    if (
        not required or not set(required) <= set(program.capability_dependencies)
        or not host.semantic_program_is_current_trusted(program) or not _ability_matches(ability, program)
        or "cost_cards" in response and "cost_objects" in response
    ):
        raise ValueError("Paid mana-value damage lacks its original cost and result authority")
    refs = response.get("cost_cards", response.get("cost_objects", ()))
    if (
        not isinstance(refs, (list, tuple)) or len(refs) != 1 or type(refs[0]) is not str
        or refs[0] not in activation_choice_candidates(host, proposal.seat, source, _CHOICE, ability=ability)
    ):
        raise ValueError("Sacrifice exactly one other legal artifact or creature you control")
    card = host._resolve_object(proposal.seat, refs[0], zones={"battlefield"})
    snapshot = PaidSacrificeManaValueSnapshot(card.logical_object_id, public_mana_value(host._effective_card_data(card)))
    fact = PaidDamageManaValueFact(program.key, source.logical_object_id, card.object_id, card.ref, snapshot)
    return PAID_DAMAGE_MANA_VALUE_CONTEXT, fact.to_dict()


def validate_paid_damage_mana_value_payment(paid_objects, context):
    if context[0] == PAID_DAMAGE_MANA_VALUE_CONTEXT:
        fact = PaidDamageManaValueFact.from_dict(context[1])
        if tuple(paid_objects) != (fact.paid_object_id,):
            raise GameRuleError("Paid mana-value damage did not commit its captured sacrifice")


def resolve_paid_damage_mana_value(item) -> int:
    fact = PaidDamageManaValueFact.from_dict(item.context.get(PAID_DAMAGE_MANA_VALUE_CONTEXT))
    if (
        item.kind != "activated_ability" or item.semantic_key != fact.semantic_key
        or item.context.get("source_logical_object_id") != fact.source_logical_object_id
        or item.context.get("cost_objects") != [fact.paid_ref]
    ):
        raise ValueError("Paid mana-value damage changed its ability or selected sacrifice")
    return fact.snapshot.mana_value
