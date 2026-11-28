from __future__ import annotations

"""Capability closure for bounded programs of mandatory typed effects."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.closed_effect_programs import has_one_closed_target_slot, CLOSED_EFFECT_PROGRAM_TEMPLATE_ID
from .fixed_effect_clause_shapes import (
    _contains_target_reference,
    closed_effect_component_capabilities,
)
from ..fractional_quantity_model import HALF_QUANTITY_KIND
from .public_quantity_mana_shapes import fixed_then_named_graveyard_mana_capabilities
from ..linked_exile_return_model import LINKED_EXILE_RETURN_OPERATION, LinkedExileReturnSpec
from ..replacement.immutable import thaw_value
from .bound_effect_program_shapes import (
    bound_effect_program_node_capabilities, has_target_controller_recipient, has_target_controller_search_role,
)


CLOSED_EFFECT_PROGRAM_MECHANIC = "closed-effect-program"
CLOSED_EFFECT_PROGRAM_CAPABILITY = (
    "resolution.effect_program.closed_components"
)


def _linked_exile_then_life_dependencies(effects, target_schema, mechanics):
    if (len(effects) != 3 or not isinstance(effects[0], Mapping)
            or effects[0].get("op") != LINKED_EXILE_RETURN_OPERATION
            or effects[2] != {"op": "life", "player": "$controller", "delta": 2}
            or target_schema is None or not has_one_closed_target_slot(target_schema)):
        return ()
    battlefield = {
        "zones": ["battlefield"], "categories": ["permanent"], "controller_relation": "opponent",
    }
    schema = thaw_value(target_schema)
    if schema not in (
        {**battlefield, "up_to": 1, "types_none": ["land"]},
        {**battlefield, "count": 1, "types_any": ["creature"]},
    ):
        return ()
    expected = LinkedExileReturnSpec(timing="source_departure", schema_version=2)
    if thaw_value(effects[0].get("spec")) != expected.to_dict():
        return ()

    from .linked_exile_return_shapes import linked_exile_return_node_capabilities
    from .permanent_predicate_capability_shapes import direct_target_predicate_capabilities

    linked = linked_exile_return_node_capabilities(
        effects=effects[:2], target_schema=target_schema, mechanic_ids=mechanics,
    )
    life = closed_effect_component_capabilities(effects[2:], target_schema=None, mechanics=mechanics)
    if not linked or not life:
        return ()
    # The linked owner checks cardinality. The predicate owner reads the one-target domain.
    predicate_schema = {**schema, "count": 1}
    predicate_schema.pop("up_to", None)
    return (*linked, *life, "target.revalidate_resolution", *direct_target_predicate_capabilities(predicate_schema))


def closed_effect_program_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Own a bounded ordered program when every flattened effect is closed."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    from .sequential_condition_shapes import sequential_condition_node_capabilities,SEQUENTIAL_CONDITION_MECHANIC
    if SEQUENTIAL_CONDITION_MECHANIC in mechanics:
        return sequential_condition_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if "bound-effect-program" in mechanics:
        return bound_effect_program_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
        )
    if (
        CLOSED_EFFECT_PROGRAM_MECHANIC not in mechanics
        or not 2 <= len(effects) <= 8
        or any(has_target_controller_recipient(effect) or has_target_controller_search_role(effect)
               for effect in effects)
    ):
        return ()
    named_mana = fixed_then_named_graveyard_mana_capabilities(
        effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
    )
    if named_mana:
        return (CLOSED_EFFECT_PROGRAM_CAPABILITY, *named_mana)
    target_effects = tuple(
        effect for effect in effects if _contains_target_reference(effect)
    )
    if target_schema is None:
        if target_effects:
            return ()
    elif not target_effects or not has_one_closed_target_slot(target_schema):
        return ()

    component_mechanics = mechanics - {CLOSED_EFFECT_PROGRAM_MECHANIC}
    dependencies = {CLOSED_EFFECT_PROGRAM_CAPABILITY}
    linked_then_life = _linked_exile_then_life_dependencies(effects, target_schema, component_mechanics)
    if linked_then_life:
        return tuple(sorted(dependencies.union(linked_then_life)))
    if target_effects:
        for target_effect in target_effects:
            target_dependencies = closed_effect_component_capabilities((target_effect,),target_schema=target_schema,mechanics=component_mechanics)
            if not target_dependencies:return ()
            dependencies.update(target_dependencies)
    for effect in effects:
        if _contains_target_reference(effect):
            continue
        component = closed_effect_component_capabilities(
            (effect,),
            target_schema=None,
            mechanics=component_mechanics,
        )
        if not component:
            return ()
        dependencies.update(component)
    return tuple(sorted(dependencies))


def _ordered_body_runtime_dependencies(program, schema):
    from ..compiler.program_composition import composed_spell_family_runtime_dependencies
    from ..compiler.public_query_effect_amounts import public_query_amount_shape_context

    composed = composed_spell_family_runtime_dependencies(
        program, capability=CLOSED_EFFECT_PROGRAM_CAPABILITY, mechanic=CLOSED_EFFECT_PROGRAM_MECHANIC,
    )
    if composed is not None:
        if CLOSED_EFFECT_PROGRAM_CAPABILITY not in composed:
            raise ValueError("Composed ordered programs require the closed component owner")
        return composed

    effects = program.effects
    mechanics = set(program.coverage) | {CLOSED_EFFECT_PROGRAM_MECHANIC}
    if effects and effects[-1].get("op") == "apply_if_public_condition":
        from .resolution_condition_shapes import RESOLUTION_CONDITION_MECHANIC, resolution_condition_node_capabilities

        owned = resolution_condition_node_capabilities(
            effects=effects, target_schema=schema,
            mechanic_ids=mechanics | {RESOLUTION_CONDITION_MECHANIC},
            cost_schema=getattr(program, "cost_schema", None),
        )
        if CLOSED_EFFECT_PROGRAM_CAPABILITY not in owned:
            raise ValueError("Conditional ordered programs require their complete prefix and branches")
        return owned

    suffix_dependencies = ()
    if any(effect.get("op") == "encode_cipher" for effect in effects):
        from ..cipher_model import CIPHER_CAPABILITY, CIPHER_HANDLER

        if (
            effects[-1] != {"op": "encode_cipher"}
            or getattr(program, "event", None) != "resolve"
            or getattr(program, "active_zone", None) != "stack"
            or sum(effect.get("op") == "encode_cipher" for effect in effects) != 1
            or not any(handler.get("handler_id") == CIPHER_HANDLER for handler in getattr(program, "handlers", ()))
        ):
            raise ValueError("Ordered Cipher programs require one terminal owned encoding instruction")
        effects = effects[:-1]
        suffix_dependencies = (CIPHER_CAPABILITY,)

    if not 2 <= len(effects) <= 8:
        raise ValueError("Closed ordered programs require two to eight effects")
    named_mana = fixed_then_named_graveyard_mana_capabilities(
        effects=effects, target_schema=schema, mechanic_ids=mechanics,
    )
    if named_mana:
        return tuple(sorted({CLOSED_EFFECT_PROGRAM_CAPABILITY, *named_mana, *suffix_dependencies}))
    if any(isinstance(value, Mapping) and value.get("kind") == HALF_QUANTITY_KIND
           for effect in effects for value in (effect.get("amount"), effect.get("count"))):
        owned = closed_effect_program_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=program.coverage,
        )
        if not owned:
            raise ValueError("Ordered fractional effects require all closed result owners")
    else:
        context = public_query_amount_shape_context(effects, mechanics, target_schema=schema)
        if context is None:
            raise ValueError("Ordered programs require their validated amount shapes")
        effects, mechanics = context
        owned = closed_effect_program_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=mechanics,
        )
        if not owned:
            raise ValueError("Ordered programs require every complete child owner")
    return tuple(sorted({*owned, *suffix_dependencies}))


def closed_effect_program_runtime_dependencies(program):
    """Keep the persisted ordered-program kind bound to its mutation owner."""
    from .graveyard_exchange_model import graveyard_exchange_program_dependencies
    from .death_counter_shuffle import death_counter_shuffle_dependencies
    from .bound_effect_program_shapes import source_target_counter_batch_program_dependencies
    from .qualified_token_creators import qualified_token_creator_program_dependencies
    from .fixed_effect_clause_shapes import (
        equipped_counter_proliferate_runtime_dependencies, fixed_proliferate_sequence_runtime_dependencies,
    )

    from .linked_exile_activations import linked_exile_activation_runtime_dependencies

    if exchange := graveyard_exchange_program_dependencies(program):
        return exchange
    if departure := death_counter_shuffle_dependencies(program):
        return departure
    if linked_activation := linked_exile_activation_runtime_dependencies(program):
        return linked_activation
    if proliferate := fixed_proliferate_sequence_runtime_dependencies(program):
        return proliferate
    if paired := source_target_counter_batch_program_dependencies(program):
        return paired
    if qualified := qualified_token_creator_program_dependencies(program):
        return qualified
    from .prepared_creature_power_damage_shapes import untapped_controller_damage_runtime_dependencies

    if counter_proliferate := equipped_counter_proliferate_runtime_dependencies(program):
        return counter_proliferate

    if prepared_damage := untapped_controller_damage_runtime_dependencies(program):
        return prepared_damage
    if any('optional_target_result' in effect for effect in program.effects):
        from .optional_target_results import optional_target_result_runtime_dependencies
        return optional_target_result_runtime_dependencies(program)
    from .target_recipient_results import target_recipient_result_runtime_dependencies
    from .sequential_condition_shapes import sequential_condition_runtime_dependencies
    sequential=sequential_condition_runtime_dependencies(program)
    if sequential:return sequential
    recipient=target_recipient_result_runtime_dependencies(program)
    if recipient:return recipient
    schema=getattr(program,'target_schema',None)
    if isinstance(schema,Mapping) and isinstance(schema.get('modes'),Mapping):
        definitions=tuple(schema['modes'].values())
        marked=CLOSED_EFFECT_PROGRAM_MECHANIC in program.coverage or any(
            isinstance(branch,Mapping) and CLOSED_EFFECT_PROGRAM_MECHANIC in branch.get('mechanics',()) for branch in definitions)
        if not marked:return ()
        from ..compiler.modal_templates import FIXED_CHOOSE_ONE_MODAL_MECHANIC,FIXED_NONREPEATING_MODAL_MECHANIC
        from .modal_capability_shapes import FIXED_MODAL_WRAPPER_MECHANICS,fixed_choose_one_modal_branches,fixed_nonrepeating_modal_branches
        modal=FIXED_NONREPEATING_MODAL_MECHANIC if FIXED_NONREPEATING_MODAL_MECHANIC in program.coverage else FIXED_CHOOSE_ONE_MODAL_MECHANIC
        mechanics={modal,*set(program.coverage).intersection(FIXED_MODAL_WRAPPER_MECHANICS)}
        for definition in definitions:
            if not isinstance(definition,Mapping) or not isinstance(definition.get('mechanics'),(list,tuple)):
                raise ValueError('Modal ordered-program scope is malformed')
            mechanics.update(definition['mechanics'])
        decode=fixed_nonrepeating_modal_branches if FIXED_NONREPEATING_MODAL_MECHANIC in mechanics else fixed_choose_one_modal_branches
        branches=decode(effects=program.effects,target_schema=schema,mechanic_ids=tuple(sorted(mechanics)))
        if branches is None:raise ValueError('Modal ordered-program branches are malformed')
        from ..pawprint_modes import PAWPRINT_MECHANIC
        from .modal_node_capabilities import fixed_modal_capability_dependencies

        modal_owned = fixed_modal_capability_dependencies(
            effects=program.effects, target_schema=schema, mechanics=tuple(sorted(mechanics)),
            allow_pawprint_draw=PAWPRINT_MECHANIC in program.coverage,
        )
        if not modal_owned:raise ValueError('Modal ordered-program sibling branches are not independently closed')
        required=set()
        for branch in branches:
            if CLOSED_EFFECT_PROGRAM_MECHANIC not in branch.mechanics:continue
            owned=closed_effect_program_node_capabilities(effects=branch.effects,target_schema=branch.target_schema,mechanic_ids=branch.mechanics)
            if not owned:raise ValueError('Modal ordered-program branch lacks its complete ordered effect owner')
            required.update(owned)
        if CLOSED_EFFECT_PROGRAM_CAPABILITY not in required:raise ValueError('Modal ordered-program annotation has no owning branch')
        return tuple(sorted({*required,*modal_owned}))
    from ..public_zone_moves import OWNER_LIBRARY_POSITION_OPERATION
    from .targeted_return_spell_copy import LAND_PAYMENT_FIELD

    if any(effect.get("op") != OWNER_LIBRARY_POSITION_OPERATION and LAND_PAYMENT_FIELD not in effect
           and has_target_controller_recipient(effect) for effect in program.effects):
        from .optional_source_damage import OPTIONAL_SOURCE_DAMAGE_FIELD, optional_source_damage_runtime_dependencies

        if (
            len(program.effects) == 1
            and OPTIONAL_SOURCE_DAMAGE_FIELD in program.effects[0]
            and CLOSED_EFFECT_PROGRAM_MECHANIC not in program.coverage
            and program.provenance.get("template_id") != CLOSED_EFFECT_PROGRAM_TEMPLATE_ID
        ):
            return optional_source_damage_runtime_dependencies(program)

        from ..compiler.public_query_effect_amounts import public_query_amount_shape_context

        projected = public_query_amount_shape_context(program.effects, set(program.coverage), target_schema=schema)
        if projected is None:
            raise ValueError("A target controller result requires its closed amount shape")
        effects, mechanics = projected
        owned = closed_effect_program_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=mechanics,
        )
        if not owned:
            raise ValueError("A target controller result requires its preceding closed target instruction")
        return owned
    if any(has_target_controller_search_role(effect) for effect in program.effects):
        owned = closed_effect_program_node_capabilities(
            effects=program.effects, target_schema=schema, mechanic_ids=program.coverage,
        )
        if not owned:
            raise ValueError("Target controller search requires its preceding closed target instruction")
        return owned
    provenance = getattr(program, 'provenance', {})
    stored_program = isinstance(provenance, Mapping) and provenance.get('template_id') == CLOSED_EFFECT_PROGRAM_TEMPLATE_ID
    if CLOSED_EFFECT_PROGRAM_MECHANIC not in program.coverage and not stored_program:
        return ()
    if (len(program.effects) == 1
            and program.effects[0].get("op") == "apply_if_public_condition"
            and program.effects[0].get("schema_version") == 5):
        from .resolution_condition_shapes import bound_resolution_condition_runtime_dependencies

        owned = bound_resolution_condition_runtime_dependencies(program)
        if CLOSED_EFFECT_PROGRAM_CAPABILITY not in owned:
            raise ValueError("Kicked ordered programs require their complete scoped branches")
        return owned
    if (len(program.effects) == 1
            and program.effects[0].get('op') == 'offer_optional_mana_payment'
            and program.effects[0].get('schema_version') == 2):
        from .fixed_effect_payment_shapes import fixed_effect_payment_node_capabilities
        owned=fixed_effect_payment_node_capabilities(effects=program.effects,
            target_schema=schema,mechanic_ids=program.coverage)
        if CLOSED_EFFECT_PROGRAM_CAPABILITY not in owned:
            raise ValueError('Paid ordered programs require a closed payment and complete child sequence')
        return owned
    return _ordered_body_runtime_dependencies(program, schema)


__all__ = [
    "CLOSED_EFFECT_PROGRAM_CAPABILITY",
    "CLOSED_EFFECT_PROGRAM_MECHANIC",
    "closed_effect_program_node_capabilities",
    "closed_effect_program_runtime_dependencies",
]
