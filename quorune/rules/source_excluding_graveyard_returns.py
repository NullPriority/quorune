from __future__ import annotations

"""Source-excluding owned graveyard returns on source triggers and fixed activations."""

from collections.abc import Mapping

from ..replacement.immutable import thaw_value
from .event_subscriptions import FixedEventSubscription, fixed_event_subscription_condition
from .graveyard_card_targets import (
    GraveyardCardTargetError,
    GraveyardCardTargetKind,
    OwnGraveyardCardTargetSpec,
    targeted_own_graveyard_return_node_capabilities,
)


SOURCE_EXCLUDING_GRAVEYARD_RETURN_CAPABILITY = "card.return.own_graveyard_excluding_source"
_MECHANICS = ("return-to-owner-hand", "cr-115-targets")
_SIMPLE_CARD_KINDS = {GraveyardCardTargetKind.ARTIFACT_CARD, GraveyardCardTargetKind.CREATURE_CARD}
_AURA_OR_EQUIPMENT = OwnGraveyardCardTargetSpec(None, subtypes_any=("aura", "equipment"))
_SOURCE_MOVE_PRICE_CAPABILITY = "activation.source_zone_change.fixed"


def source_excluding_graveyard_return_schema(spec: OwnGraveyardCardTargetSpec) -> dict:
    if not isinstance(spec, OwnGraveyardCardTargetSpec):
        raise GraveyardCardTargetError("Source-excluding graveyard return requires one typed target")
    if spec.kind in _SIMPLE_CARD_KINDS:
        plain = OwnGraveyardCardTargetSpec(spec.kind)
    elif spec == _AURA_OR_EQUIPMENT:
        plain = _AURA_OR_EQUIPMENT
    elif spec.kind is None and len(spec.subtypes_any) == 1:
        from ..creature_subtypes import canonical_creature_subtype

        if canonical_creature_subtype(spec.subtypes_any[0]) is None:
            raise GraveyardCardTargetError("Source-excluding graveyard return requires a creature subtype")
        plain = OwnGraveyardCardTargetSpec(None, subtypes_any=spec.subtypes_any)
    else:
        raise GraveyardCardTargetError("Source-excluding graveyard return has an unsupported target domain")
    if spec != plain:
        raise GraveyardCardTargetError("Source-excluding graveyard return target qualifiers are closed")
    return {**plain.to_target_schema(), "source_exclusion": True}


def source_excluding_graveyard_return_node_capabilities(*, effects, target_schema, mechanic_ids):
    schema = thaw_value(target_schema)
    if not isinstance(schema, Mapping) or schema.get("source_exclusion") is not True:
        return ()
    base_schema = {key: value for key, value in schema.items() if key != "source_exclusion"}
    try:
        spec = OwnGraveyardCardTargetSpec.from_target_schema(base_schema)
        if source_excluding_graveyard_return_schema(spec) != schema:
            return ()
    except (TypeError, ValueError):
        return ()
    base = targeted_own_graveyard_return_node_capabilities(
        effects=effects, target_schema=base_schema, mechanic_ids=mechanic_ids,
    )
    return (*base, SOURCE_EXCLUDING_GRAVEYARD_RETURN_CAPABILITY) if base else ()


def _source_trigger_frame(program):
    if program.active_zone != "battlefield":
        return False
    if program.event == "creature.dies.self":
        return program.event_condition is None
    source = {"field": "card", "op": "eq", "value": "$source.ref"}
    pair = fixed_event_subscription_condition((
        FixedEventSubscription("permanent.enter", source),
        FixedEventSubscription("permanent.leave", source),
    ))
    return program.event == "permanent.enter" and thaw_value(program.event_condition) == pair


def _fixed_source_move_activation_frame(program):
    if (program.active_zone not in {"battlefield", "graveyard"} or len(program.handlers) != 1
            or program.cost_schema is None or program.event_condition is not None):
        return False

    from ..abilities import ActivatedAbility
    from ..compiler.activated_costs import activated_ability_cost
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

    schema = thaw_value(program.target_schema)
    graveyard = program.active_zone == "graveyard"
    if schema.get("types_any") != ["creature"] and not (
        graveyard and schema.get("subtypes_any") == ["aura", "equipment"]
    ):
        return False
    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    expected = ActivatedAbility(
        ability_id=ability.ability_id, line_index=ability.line_index, oracle_line=ability.oracle_line,
        cost_text=ability.cost_text, effect_text=ability.effect_text, zones=(program.active_zone,),
        mana=ability.mana, sacrifice_source=not graveyard, exile_source=graveyard,
        builtin_semantic_key=program.key, target_schema=ability.target_schema,
    )
    return (ability == expected and any(ability.mana.values())
            and thaw_value(ability.target_schema) == schema
            and activated_ability_cost(ability) == thaw_value(program.cost_schema))


def _optional_source_death_payment_dependencies(program):
    if (program.event != "creature.dies.self" or len(program.effects) != 1
            or (thaw_value(program.target_schema) or {}).get("types_any") != ["creature"]):
        return ()
    from ..compiler.optional_payment_templates import (
        FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC, OPTIONAL_MANA_PAYMENT_OPERATION,
    )
    from .fixed_effect_clause_shapes import fixed_optional_mana_payment_node_capabilities

    wrapper = program.effects[0]
    if set(wrapper) != {"op", "player", "cost", "effects"} or wrapper.get("op") != OPTIONAL_MANA_PAYMENT_OPERATION:
        return ()
    required = fixed_optional_mana_payment_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=(FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC, *_MECHANICS),
    )
    return required if SOURCE_EXCLUDING_GRAVEYARD_RETURN_CAPABILITY in required else ()


def source_excluding_graveyard_return_runtime_dependencies(program):
    from ..event_card_return import death_land_exile_runtime_dependencies, has_death_source_exile

    if program.effects and has_death_source_exile(program.effects[0]):
        return death_land_exile_runtime_dependencies(program)
    required = source_excluding_graveyard_return_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=_MECHANICS,
    )
    if required and program.event == "activate" and _fixed_source_move_activation_frame(program):
        return (*required, _SOURCE_MOVE_PRICE_CAPABILITY)
    if _source_trigger_frame(program):
        schema = thaw_value(program.target_schema) or {}
        if required and schema.get("subtypes_any") != ["aura", "equipment"]:
            return required
        if isinstance(schema, Mapping) and (optional := _optional_source_death_payment_dependencies(program)):
            return optional
    raise GraveyardCardTargetError(
        "Source-excluding graveyard return requires its exact self-zone trigger and target "
        "or a matching fixed source-moving activation"
    )
