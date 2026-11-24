from __future__ import annotations

"""Closed cost and independently owned result binding for payment v2."""

from typing import Any, Iterable, Mapping, Sequence

from ..fixed_effect_payment import (
    FIXED_EFFECT_PAYMENT_MECHANIC, MANDATORY_SOURCE_COUNTER_FOLLOWUP_MECHANIC,
    RETURN_PAYMENT_KIND, FixedEffectPaymentSpec,
    mandatory_source_counter_followup_parts, uses_mandatory_source_counter_followup,
)
from ..util import stable_json
from ..compiler.optional_payment_templates import OPTIONAL_MANA_PAYMENT_OPERATION
from .blight_model import blight_decline_branch_is_closed
from .hybrid_resolution_payment import HYBRID_RESOLUTION_PAYMENT_KIND
from .targeted_life_decline import is_targeted_life_decline_effect, targeted_life_decline_node_capabilities
from .scheduled_life_return import (
    is_scheduled_life_return, scheduled_life_return_node_dependencies, scheduled_life_return_program_dependencies,
)


def fixed_payment_result_capabilities(*,effects:Sequence[Mapping[str,Any]],target_schema:Mapping[str,Any]|None,
                                     mechanic_ids:Iterable[str])->tuple[str,...]:
    from .fixed_effect_clause_shapes import closed_effect_component_capabilities
    from .closed_effect_program_shapes import closed_effect_program_node_capabilities
    from .fixed_controller_effect_shapes import fixed_controller_effect_sequence_node_capabilities,fixed_counter_controller_effect_sequence_node_capabilities
    mechanics=set(mechanic_ids)-{FIXED_EFFECT_PAYMENT_MECHANIC}
    if len(effects)==1:
        from .affected_player_discard_capability_shapes import fixed_context_player_discard_node_capabilities
        from .event_player_result_shapes import fixed_event_player_result_node_capabilities
        from .event_player_choice_shapes import event_player_sacrifice_node_capabilities
        from .mill_capability_shapes import fixed_mill_node_capabilities
        for resolver in (fixed_context_player_discard_node_capabilities,fixed_event_player_result_node_capabilities,
                         event_player_sacrifice_node_capabilities,fixed_mill_node_capabilities):
            result=resolver(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
            if result:return result
        if effects[0].get('op')=='fixed_library_selection':
            from .library_selection_capability_shapes import fixed_library_selection_node_capabilities
            return fixed_library_selection_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
        from .reanimation_capability_shapes import fixed_target_reanimation_node_capabilities
        from .node_capability_shapes import fixed_source_characteristics_node_capabilities
        for resolver in (fixed_target_reanimation_node_capabilities,fixed_source_characteristics_node_capabilities):
            result=resolver(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
            if result:return result
        return closed_effect_component_capabilities(effects,target_schema=target_schema,mechanics=mechanics)
    from .fixed_effect_clause_shapes import fixed_effect_clause_sequence_node_capabilities
    from .target_player_sequences import target_player_sequence_node_capabilities
    from .target_set_result_sequences import target_set_result_sequence_capabilities
    for resolver in (fixed_controller_effect_sequence_node_capabilities,fixed_counter_controller_effect_sequence_node_capabilities,
                     fixed_effect_clause_sequence_node_capabilities,target_player_sequence_node_capabilities,
                     target_set_result_sequence_capabilities,closed_effect_program_node_capabilities):
        result=resolver(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
        if result:return result
    return ()


_MANDATORY_COUNTER_DAMAGE_SCHEMA = {
    "zones": ["player", "battlefield"], "categories": ["player", "permanent"],
    "predicate": "damageable", "count": 1,
}


def _mandatory_source_counter_node_capabilities(wrapper, target_schema, mechanics):
    try:
        payment, child = mandatory_source_counter_followup_parts(wrapper)
    except (TypeError, ValueError, KeyError):
        return ()
    if child["op"] == "damage":
        if (stable_json(child) != stable_json({"op": "damage", "source": "$source", "target": "$target.0", "amount": 1})
                or stable_json(target_schema) != stable_json(_MANDATORY_COUNTER_DAMAGE_SCHEMA)):
            return ()
    elif (target_schema is not None or child.get("controller") != "$controller"
          or type(child.get("quantity")) is not int or child["quantity"] != 1
          or not isinstance(child.get("characteristics"), Mapping) or "copy_of" in child):
        return ()
    result = fixed_payment_result_capabilities(effects=(child,), target_schema=target_schema, mechanic_ids=mechanics)
    return (*payment.capabilities, *result) if result else ()


def _mandatory_source_counter_program_dependencies(program, wrapper):
    owned = _mandatory_source_counter_node_capabilities(wrapper, program.target_schema, program.coverage)
    if (not owned or len(program.effects) != 1 or program.effects[0] != wrapper
            or program.active_zone != "battlefield" or program.cost_schema is not None or program.handlers):
        raise ValueError("Mandatory counter payment requires one complete battlefield trigger")
    effect = wrapper["effects"][0]
    if effect["op"] == "damage":
        from .event_subscriptions import FixedEventSubscription, fixed_event_subscription_condition

        source = {"field": "card", "op": "eq", "value": "$source.ref"}
        expected = fixed_event_subscription_condition(tuple(
            FixedEventSubscription(event, source) for event in ("creature.attacks", "creature.blocks")
        ))
        if program.event != "creature.attacks" or stable_json(program.event_condition) != stable_json(expected):
            raise ValueError("Mandatory counter damage requires its original attack and block subscriptions")
        return (*owned, "trigger.event.normalized_public_action", "trigger.subscription.fixed_multi_event")
    if program.event != "spell.cast" or program.event_condition is not None:
        raise ValueError("Mandatory counter token creation requires its original any-player cast event")
    return (*owned, "trigger.event.normalized_spell_cast")


SOURCE_COUNTER_UPKEEP_DRAW_COVERAGE = "source-counter-upkeep-draw"


def source_counter_upkeep_draw_condition():
    from ..continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec

    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.SOURCE_COUNTER_AT_LEAST, amount=1, counter_name="+1/+1",
    )


def _source_counter_upkeep_draw_dependencies(program):
    from ..continuous_conditions import FIXED_PUBLIC_STATE_INTERVENING_COVERAGE, FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD
    from .cumulative_object_upkeep import CONTROLLER_UPKEEP

    coverage = set(program.coverage)
    condition = {"all": [CONTROLLER_UPKEEP, {
        "field": FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, "op": "truthy", "value": True,
        "condition": source_counter_upkeep_draw_condition().to_dict(),
    }]}
    payment = FixedEffectPaymentSpec("remove_source_counter", schema_version=4, counter_name="+1/+1",
                                     source_ref="$source.zone_object")
    effect = {
        "op": OPTIONAL_MANA_PAYMENT_OPERATION, "schema_version": 2, "player": "$controller",
        "payment": payment.to_dict(),
        "effects": [{"op": "draw", "player": "$controller", "count": 1, "private": True}],
    }
    if (
        (program.event, program.active_zone) != ("step.begin", "battlefield")
        or program.target_schema is not None or program.cost_schema is not None or program.handlers
        or not {"intervening_condition", FIXED_PUBLIC_STATE_INTERVENING_COVERAGE} <= coverage
        or stable_json(program.event_condition) != stable_json(condition)
        or stable_json(program.effects) != stable_json((effect,))
    ):
        raise ValueError("Upkeep counter draw requires the original source threshold, price and private draw")
    owned = fixed_effect_payment_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not owned:
        raise ValueError("Upkeep counter draw requires the existing fixed-payment owner")
    return (*owned, "trigger.condition.fixed_public_state")


def fixed_payment_event_player_runtime_dependencies(program):
    """Keep occurrence and payment authority for each closed result."""
    if SOURCE_COUNTER_UPKEEP_DRAW_COVERAGE in getattr(program, "coverage", ()):
        return _source_counter_upkeep_draw_dependencies(program)
    if MANDATORY_SOURCE_COUNTER_FOLLOWUP_MECHANIC in getattr(program, "coverage", ()):
        if len(program.effects) != 1 or not uses_mandatory_source_counter_followup(program.effects[0]):
            raise ValueError("Mandatory counter follow-up requires its explicit payment version")
    from .entry_player_counter_payment import (
        entry_player_counter_payment_dependencies, uses_entry_player_counter_payment,
    )
    from .life_gain_counted_results import life_gain_counter_payment_program_dependencies
    players={'$context.player','$context.target','$context.defending_player','$context.previous_controller','$context.controller'}
    def references(value):
        if isinstance(value,Mapping):return set().union(*(references(child) for child in value.values()))
        if isinstance(value,(tuple,list)):return set().union(*(references(child) for child in value))
        return {value} if isinstance(value,str) and value in players else set()
    from ..spell_caster_sacrifice_payment import spell_caster_sacrifice_payment_program_dependencies
    required=set(life_gain_counter_payment_program_dependencies(program))
    required.update(spell_caster_sacrifice_payment_program_dependencies(program))
    from .optional_sacrifice_stats import optional_sacrifice_stat_program_dependencies
    required.update(optional_sacrifice_stat_program_dependencies(program))
    from .fractional_life_payment import fractional_life_payment_program_dependencies
    required.update(fractional_life_payment_program_dependencies(program))
    from .event_player_result_shapes import _event_player_reference_is_closed
    def visit(value):
        if isinstance(value,Mapping):
            if uses_mandatory_source_counter_followup(value):
                required.update(_mandatory_source_counter_program_dependencies(program, value))
                return
            if uses_entry_player_counter_payment(value):
                required.update(entry_player_counter_payment_dependencies(program, value))
                return
            if is_scheduled_life_return(value):
                required.update(scheduled_life_return_program_dependencies(program, value))
                return
            if value.get('op')==OPTIONAL_MANA_PAYMENT_OPERATION and value.get('schema_version')==2:
                payment=value.get('payment')
                if not isinstance(payment,Mapping):raise ValueError('Fixed payment descriptor is unavailable')
                refs=references(value.get('effects',())) | references(value.get('declined_effects',()))
                if (refs or is_targeted_life_decline_effect(value)
                        or payment.get('kind') in ('source_sacrifice', HYBRID_RESOLUTION_PAYMENT_KIND, RETURN_PAYMENT_KIND)):
                    owned=fixed_effect_payment_node_capabilities(effects=(value,),target_schema=program.target_schema,mechanic_ids=program.coverage)
                    if not owned or any(not _event_player_reference_is_closed(program,ref) for ref in refs):
                        raise ValueError('Fixed payment result lacks its closed occurrence-player scope')
                    required.update(owned)
            for child in value.values():visit(child)
        elif isinstance(value,(tuple,list)):
            for child in value:visit(child)
    visit(program.effects)
    return tuple(sorted(required))


def fixed_effect_payment_node_capabilities(*,effects:Sequence[Mapping[str,Any]],target_schema:Mapping[str,Any]|None,
                                         mechanic_ids:Iterable[str])->tuple[str,...]:
    mechanics=set(mechanic_ids)
    if FIXED_EFFECT_PAYMENT_MECHANIC not in mechanics or len(effects)!=1:return ()
    wrapper=effects[0]
    if uses_mandatory_source_counter_followup(wrapper):
        return _mandatory_source_counter_node_capabilities(wrapper, target_schema, mechanics)
    from .entry_player_counter_payment import (
        ENTRY_PLAYER_COUNTER_PAYMENT_MECHANIC, entry_player_counter_payment_capabilities,
        is_entry_player_counter_payment, uses_entry_player_counter_payment,
    )

    if uses_entry_player_counter_payment(wrapper):
        if (target_schema is not None or ENTRY_PLAYER_COUNTER_PAYMENT_MECHANIC not in mechanics
                or not is_entry_player_counter_payment(wrapper)):
            return ()
        return entry_player_counter_payment_capabilities()
    if is_scheduled_life_return(wrapper):
        return scheduled_life_return_node_dependencies(wrapper, target_schema, mechanics)
    if is_targeted_life_decline_effect(wrapper):
        return targeted_life_decline_node_capabilities(wrapper, target_schema, mechanics)
    from ..spell_caster_sacrifice_payment import (
        spell_caster_sacrifice_payment_capabilities, uses_spell_caster_sacrifice_payment,
    )
    if uses_spell_caster_sacrifice_payment(wrapper):
        if target_schema is not None:
            return ()
        try:
            return spell_caster_sacrifice_payment_capabilities(wrapper)
        except (KeyError, TypeError, ValueError):
            return ()
    if isinstance(wrapper,Mapping) and wrapper.get('schema_version') == 11:
        if target_schema is not None:return ()
        from ..cumulative_graveyard_upkeep import GraveyardLibraryCostSpec
        try:return GraveyardLibraryCostSpec.from_effect(wrapper).capabilities
        except (KeyError,TypeError,ValueError):return ()
    if isinstance(wrapper,Mapping) and wrapper.get('schema_version') == 10:
        if target_schema is not None:return ()
        from ..cumulative_effect_cost_upkeep import UpkeepEffectCostSpec
        try:return UpkeepEffectCostSpec.from_effect(wrapper).capabilities
        except (KeyError,TypeError,ValueError):return ()
    if isinstance(wrapper,Mapping) and wrapper.get('schema_version') == 9:
        if target_schema is not None:return ()
        from ..cumulative_alternative_mana_upkeep import CumulativeAlternativeManaSpec
        try:return CumulativeAlternativeManaSpec.from_effect(wrapper).capabilities
        except (KeyError,TypeError,ValueError):return ()
    if isinstance(wrapper,Mapping) and wrapper.get('schema_version') == 8:
        if target_schema is not None:return ()
        from ..cumulative_counter_upkeep import CumulativeCounterUpkeepSpec
        try:return CumulativeCounterUpkeepSpec.from_effect(wrapper).capabilities
        except (KeyError,TypeError,ValueError):return ()
    if isinstance(wrapper,Mapping) and wrapper.get('schema_version') == 7:
        if target_schema is not None:return ()
        from ..cumulative_object_upkeep import CumulativeObjectUpkeepSpec
        try:return CumulativeObjectUpkeepSpec.from_effect(wrapper).capabilities
        except (KeyError,TypeError,ValueError):return ()
    if isinstance(wrapper,Mapping) and wrapper.get('schema_version') == 6:
        if target_schema is not None:return ()
        from ..cast_source_sacrifice_payment import cast_source_sacrifice_spec
        try:return cast_source_sacrifice_spec(wrapper).capabilities
        except (KeyError,TypeError,ValueError):return ()
    if isinstance(wrapper, Mapping) and wrapper.get('schema_version') == 4:
        from .reflexive_payment_shapes import reflexive_payment_node_capabilities
        return reflexive_payment_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    if isinstance(wrapper, Mapping) and wrapper.get('schema_version') == 3:
        from ..source_maintenance import SOURCE_MAINTENANCE_MECHANIC, SourceMaintenanceSpec
        if target_schema is not None or SOURCE_MAINTENANCE_MECHANIC not in mechanics:
            return ()
        try:
            spec = SourceMaintenanceSpec.from_effect(wrapper)
        except (KeyError, TypeError, ValueError):
            return ()
        if wrapper['player'] != '$controller' or wrapper['source'] != '$source.zone_object':
            return ()
        payment = spec.payment
        if payment is not None and (
            payment.kind in {'discard','exile','mana_discard'} and payment.predicate.owner != '$controller'
            or payment.kind in {'sacrifice','tap'} and payment.predicate.controller != '$controller'
            or payment.kind in {'remove_source_counter','source_sacrifice'} and payment.source_ref!='$source.zone_object'
        ):
            return ()
        return spec.capabilities
    fields={'op','schema_version','player','payment','effects'}
    if isinstance(wrapper, Mapping) and "declined_effects" in wrapper:
        fields.add("declined_effects")
    if not isinstance(wrapper,Mapping) or set(wrapper) not in (fields,fields|{'cost'}) or wrapper.get('op')!=OPTIONAL_MANA_PAYMENT_OPERATION or type(wrapper.get('schema_version'))is not int or wrapper['schema_version']!=2 or wrapper.get('player')!='$controller':return ()
    try:payment=FixedEffectPaymentSpec.from_dict(wrapper['payment'], allow_mandatory=True)
    except (ValueError,TypeError,KeyError):return ()
    if ('cost'in wrapper)!=(payment.kind=='mana') or payment.kind=='mana'and dict(wrapper['cost'])!=dict(payment.requirements):return ()
    if payment.kind in {'discard','exile','mana_discard'}and payment.predicate.owner!='$controller':return ()
    if payment.kind in {'sacrifice','tap','blight',RETURN_PAYMENT_KIND}and payment.predicate.controller!='$controller':return ()
    if payment.kind=='sacrifice' and payment.schema_version==6 and payment.predicate.exclude_ref!='$source.zone_object':return ()
    if payment.kind in {'remove_source_counter','source_sacrifice'} and payment.source_ref!='$source.zone_object':return ()
    nested=wrapper['effects']
    if "declined_effects" in wrapper:
        if target_schema is not None or not blight_decline_branch_is_closed(payment.kind, nested, wrapper["declined_effects"]):
            return ()
        nested = wrapper["declined_effects"]
    if not isinstance(nested,(list,tuple))or not 1<=len(nested)<=4 or any(not isinstance(e,Mapping)or e.get('op')in {'offer_optional_effect',OPTIONAL_MANA_PAYMENT_OPERATION}for e in nested):return ()
    if payment.kind == RETURN_PAYMENT_KIND and any(effect.get("card") == "$source" for effect in nested):
        return ()
    dependencies=set(payment.capabilities)
    components=fixed_payment_result_capabilities(effects=nested,target_schema=target_schema,
        mechanic_ids=mechanics-{FIXED_EFFECT_PAYMENT_MECHANIC})
    if not components:return ()
    dependencies.update(components)
    return tuple(sorted(dependencies))
