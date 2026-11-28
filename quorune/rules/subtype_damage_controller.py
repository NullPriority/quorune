"""Bind the original subtype-qualified damage dealer to its controller's result."""

from collections.abc import Mapping

from ..creature_subtypes import canonical_creature_subtype
from ..replacement.immutable import FrozenMap
from ..target_controller_reference import damage_dealer_controller_reference, is_damage_dealer_controller_reference
from .effect_shape_walkers import nested_effects


CAPABILITY = "resolution.damage_dealer.subtype_controller_result"
MECHANIC = "committed-subtype-damage-controller-result"
CHOICE_OPERATION = "choose_damage_dealer_token"
SUBTYPE_FIELD = "committed_damage_dealer_subtype"
TOKEN_VARIANT = "subtype_combat_damage_controller_token"
LIFE_VARIANT = "subtype_damage_controller_life"
_REFERENCE_VERSION = 2


def subtype_damage_condition(subtype, *, combat_token):
    source = {"field": "source_subtypes", "op": "contains_any", "value": [subtype]}
    if not combat_token:
        return source
    return {"all": [source, {"field": "combat", "op": "truthy", "value": True},
                     {"field": "target_kind", "op": "eq", "value": "player"}]}


def subtype_damage_effect(subtype, *, combat_token):
    from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

    player = damage_dealer_controller_reference(_REFERENCE_VERSION)
    if not combat_token:
        return {"op": "life", SUBTYPE_FIELD: subtype, "player": player, "source": "$source",
                "delta": ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT).to_dict()}
    from ..compiler.token_templates import fixed_token_creation_effect_template

    template = fixed_token_creation_effect_template(f"Create a 1/1 colorless {subtype.title()} creature token.")
    if template is None:
        raise ValueError("The original damage dealer needs its supported subtype token definition")
    return {"op": CHOICE_OPERATION, SUBTYPE_FIELD: subtype, "player": player,
            "token": {**template.effect, "controller": player}}


def subtype_damage_dependencies(effect, *, player=None):
    subtype = effect.get(SUBTYPE_FIELD)
    is_choice = effect.get("op") == CHOICE_OPERATION
    if type(subtype) is not str or canonical_creature_subtype(subtype) != subtype:
        raise ValueError("The result must name one canonical damage-dealing subtype")
    expected = subtype_damage_effect(subtype, combat_token=is_choice)
    if player is not None:
        if not is_choice or type(player) is not str or not player or player.startswith("$"):
            raise ValueError("The token choice must have its original damage dealer's controller")
        expected["player"] = player
        expected["token"]["controller"] = player
    if FrozenMap(effect) != FrozenMap(expected):
        raise ValueError("The subtype damage result changed its recipient, amount or token definition")
    if is_choice:
        return CAPABILITY, "token.creation.fixed_definition"
    return CAPABILITY, "life.change.effect", "quantity_expression.scalar_effect_amount"


def subtype_damage_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None, **_):
    if MECHANIC not in mechanic_ids or len(effects) != 1 or target_schema is not None or cost_schema is not None:
        return ()
    try:
        return subtype_damage_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def _has_qualified_reference(value):
    if isinstance(value, Mapping):
        return is_damage_dealer_controller_reference(value, version=_REFERENCE_VERSION) or any(
            _has_qualified_reference(child) for child in value.values()
        )
    return isinstance(value, (tuple, list)) and any(_has_qualified_reference(child) for child in value)


def subtype_damage_program_dependencies(program):
    observed = tuple(effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))
                     if SUBTYPE_FIELD in effect or effect.get("op") == CHOICE_OPERATION)
    if not observed:
        if _has_qualified_reference((program.effects, program.handlers, program.target_schema)):
            raise ValueError("This damage dealer reference needs its closed subtype result")
        return ()
    if (len(observed) != 1 or observed != tuple(program.effects) or program.handlers
            or program.target_schema is not None or program.cost_schema is not None or program.trigger_limit is not None
            or (program.event, program.active_zone) != ("damage.dealt", "battlefield")):
        raise ValueError("A subtype damage result requires its complete battlefield damage trigger")
    effect, = observed
    required = subtype_damage_dependencies(effect)
    condition = subtype_damage_condition(effect[SUBTYPE_FIELD], combat_token=effect["op"] == CHOICE_OPERATION)
    if not isinstance(program.event_condition, Mapping) or FrozenMap(program.event_condition) != FrozenMap(condition):
        raise ValueError("A subtype damage result must use its exact original committed occurrence")
    return (*required, "trigger.effect.fixed_event", "trigger.event.normalized_damage", "trigger.placement.apnap")
