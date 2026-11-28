from __future__ import annotations

"""Closed fixed all-damage shield as one ordered resolution component."""

from collections.abc import Mapping
from ..damage_modifier_state import DamagePreventionScope
from ..replacement.immutable import thaw_value
from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed


TARGET_SOURCE_NEXT_DAMAGE_FIELD = "target_source_next_damage_version"
TARGET_SOURCE_NEXT_DAMAGE_CAPABILITY = "damage.prevention.target_source_next_instance"
TARGET_COMBAT_MANA_VALUE_TEMPLATE = "target-combat-prevention-mana-value-toughness-v1"
TARGET_FULL_COUNTER_TEMPLATE = "target-full-damage-counter-aftermath-v2"
TARGET_FULL_COUNTER_CAPABILITY = "damage.prevention.target_full_counter_aftermath"
COMBAT_TOKEN_AFTERMATH_TEMPLATE = "controller-combat-prevention-token-aftermath-v1"
COMBAT_TOKEN_AFTERMATH_CAPABILITY = "damage.prevention.aftermath.token_creation"


def decode_combat_token_aftermath(value):
    """Normalize a token recipe at the prevention rule boundary."""
    from ..damage_modifier_state import CreateTokensPreventionAftermath
    from ..standard_token_abilities import standard_token_characteristics

    if not isinstance(value, Mapping) or not isinstance(value.get("characteristics"), Mapping):
        raise ValueError("Token aftermath requires a fixed creature definition")
    return CreateTokensPreventionAftermath.from_dict({
        **value, "characteristics": standard_token_characteristics(thaw_value(value["characteristics"])),
    })


def combat_token_aftermath_effect(recipe):
    return {
        "op": "create_damage_prevention_shield", "source": "$source", "subject": "$controller",
        "mode": "all", "duration": "until_end_of_turn", "damage_kind": "combat",
        "aftermath": [recipe.to_dict()],
    }


def _combat_token_aftermath_capabilities(effects, target_schema):
    from ..util import stable_json
    from .token_creation_capability_shapes import _fixed_token_effect_closure

    if target_schema is not None or len(effects) != 1:
        return ()
    try:
        raw, = effects[0]["aftermath"]
        recipe = decode_combat_token_aftermath(raw)
    except (KeyError, TypeError, ValueError):
        return ()
    if stable_json(thaw_value(effects[0])) != stable_json(combat_token_aftermath_effect(recipe)):
        return ()
    token_effect = {"op": "create_token", "controller": "$controller", "quantity": 1,
                    "name": recipe.name, "characteristics": thaw_value(recipe.characteristics)}
    closure = _fixed_token_effect_closure(token_effect)
    if closure is None:
        return ()
    return (COMBAT_TOKEN_AFTERMATH_CAPABILITY, "damage.prevention.persistent_amount", *closure[0])


def _has_combat_token_aftermath(effect):
    values = effect.get("aftermath") if isinstance(effect, Mapping) else None
    return isinstance(values, (list, tuple)) and any(
        isinstance(row, Mapping) and row.get("kind") == "create_tokens" for row in values
    )


def combat_token_aftermath_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    marked = any(_has_combat_token_aftermath(effect) for effect in nested_effects(program.effects))
    if not marked and COMBAT_TOKEN_AFTERMATH_TEMPLATE not in str(program.provenance.get("template_id") or ""):
        return ()
    required = _combat_token_aftermath_capabilities(program.effects, program.target_schema)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or program.handlers or program.cost_schema is not None):
        raise ValueError("Combat prevention tokens require their complete original spell")
    return required


def target_full_counter_aftermath_effect():
    return {"op": "create_damage_prevention_shield", "source": "$source", "subject": "$target.0",
            "mode": "all", "duration": "until_end_of_turn", "aftermath": [
                {"kind": "place_counters", "subject": "$target.0", "placing_player": "$controller",
                 "counter_name": "+1/+1", "per_prevented": 1, "fixed_amount": 0, "schema_version": 2},
            ]}


def _target_full_counter_capabilities(*, effects, target_schema, cost_schema):
    if cost_schema is not None or len(effects) != 1 or not effects[0].get("aftermath"):
        return ()
    from ..compiler.direct_target import direct_permanent_target_spec
    from ..util import stable_json

    expected = direct_permanent_target_spec("target creature you control").to_target_schema()
    if (target_schema != expected or type(target_schema["count"]) is not int
            or stable_json(thaw_value(effects)) != stable_json((target_full_counter_aftermath_effect(),))):
        return ()
    return (TARGET_FULL_COUNTER_CAPABILITY, "damage.prevention.persistent_amount", "target.revalidate_resolution",
            "counter.placement.quantity_replacement")


def _target_full_counter_aftermath_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    template = str(program.provenance.get("template_id") or "").split("+")
    marked = any(
        result.get("kind") == "place_counters" and "schema_version" in result
        for effect in nested_effects(program.effects)
        for result in effect.get("aftermath", ()) if isinstance(result, Mapping)
    )
    if not (marked or TARGET_FULL_COUNTER_TEMPLATE in template
            or TARGET_FULL_COUNTER_CAPABILITY in program.capability_dependencies):
        return ()
    required = _target_full_counter_capabilities(
        effects=program.effects, target_schema=program.target_schema, cost_schema=program.cost_schema,
    )
    if not required or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield") or program.handlers:
        raise ValueError("A full damage counter aftermath requires its exact self-entry and controlled creature target")
    return required


def targeted_outgoing_combat_shield():
    from ..object_predicate import ObjectQuerySpec

    return {
        "op": "create_damage_prevention_shield", "source": "$source", "subject": "*",
        "chosen_source": "$target.0", "mode": "all", "duration": "until_end_of_turn",
        "damage_kind": "combat",
        "source_predicate": ObjectQuerySpec(zones=("battlefield",), known_to_actor=True).to_dict(),
    }


def target_combat_mana_value_effects():
    from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

    return (
        targeted_outgoing_combat_shield(),
        {"op": "modify_stats_until_end_of_turn", "card": "$target.0", "power": 0,
         "toughness": ScalarEffectAmountSpec(ScalarAmountOrigin.TARGET, characteristic="mana_value").to_dict()},
    )


def _target_combat_mana_value_capabilities(*, effects, target_schema, cost_schema):
    if cost_schema is not None or len(effects) != 2 or effects[0].get("mode") != "all":
        return ()
    from ..compiler.direct_target import direct_permanent_target_spec
    from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY
    from ..util import stable_json

    expected = direct_permanent_target_spec("target creature").to_target_schema()
    if (target_schema != expected or type(target_schema["count"]) is not int
            or stable_json(thaw_value(effects)) != stable_json(target_combat_mana_value_effects())):
        return ()
    return ("damage.prevention.persistent_amount", "target.revalidate_resolution",
            "continuous.resolution.fixed_characteristics_until_end_of_turn", SCALAR_AMOUNT_CAPABILITY)


def targeted_prevention_result_node_capabilities(*, effects, target_schema, cost_schema=None):
    values = {"effects": effects, "target_schema": target_schema, "cost_schema": cost_schema}
    return (target_source_next_damage_node_capabilities(**values) or _target_combat_mana_value_capabilities(**values)
            or _target_full_counter_capabilities(**values))


def targeted_prevention_result_runtime_dependencies(program):
    if required := combat_token_aftermath_runtime_dependencies(program):
        return required
    if program.provenance.get("template_id") != TARGET_COMBAT_MANA_VALUE_TEMPLATE:
        return (_target_full_counter_aftermath_runtime_dependencies(program)
                or target_source_next_damage_runtime_dependencies(program))
    required = _target_combat_mana_value_capabilities(
        effects=program.effects, target_schema=program.target_schema, cost_schema=program.cost_schema,
    )
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or program.handlers or program.event_condition is not None):
        raise ValueError("Targeted outgoing combat prevention needs its original mana-value toughness result")
    return required


def target_source_next_damage_effects():
    from ..compiler.prevention_templates import _source_predicate
    from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

    return (
        {"op": "life", "player": "$controller",
         "delta": ScalarEffectAmountSpec(ScalarAmountOrigin.TARGET, characteristic="power").to_dict()},
        {"op": "create_damage_prevention_shield", "source": "$source", "subject": "*",
         "chosen_source": "$target.0", "mode": "next_instance", "duration": "until_end_of_turn",
         "source_predicate": _source_predicate(None).to_dict(), TARGET_SOURCE_NEXT_DAMAGE_FIELD: 1},
    )


def target_source_next_damage_node_capabilities(*, effects, target_schema, cost_schema=None):
    if (cost_schema is not None or len(effects) != 2 or not isinstance(effects[1], Mapping)
            or TARGET_SOURCE_NEXT_DAMAGE_FIELD not in effects[1]):
        return ()
    from ..compiler.direct_target import direct_permanent_target_spec
    from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY
    from ..util import stable_json

    if (not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or target_schema != direct_permanent_target_spec("target creature").to_target_schema()
            or stable_json(thaw_value(effects)) != stable_json(target_source_next_damage_effects())):
        return ()
    return (TARGET_SOURCE_NEXT_DAMAGE_CAPABILITY, "damage.prevention.persistent_amount",
            "target.revalidate_resolution", "life.change.effect", SCALAR_AMOUNT_CAPABILITY)


def target_source_next_damage_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    marked = any(
        TARGET_SOURCE_NEXT_DAMAGE_FIELD in effect or (
            effect.get("op") == "create_damage_prevention_shield"
            and effect.get("chosen_source") == "$target.0" and effect.get("mode") == "next_instance"
        )
        for effect in nested_effects((program.effects, program.handlers))
    )
    if not marked and TARGET_SOURCE_NEXT_DAMAGE_CAPABILITY not in program.capability_dependencies:
        return ()
    dependencies = target_source_next_damage_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, cost_schema=program.cost_schema,
    )
    if not dependencies or (program.event, program.active_zone) != ("resolve", "stack") or program.handlers:
        raise ValueError("Target source next damage requires one complete targeted spell and its ordered effects")
    return dependencies


def fixed_chosen_source_damageable_capabilities(*, effects, target_schema, mechanic_ids):
    """Prove the ordinary all-damage shield for one declared damageable target."""
    from ..compiler.prevention_templates import _chosen_source_effect

    if (len(effects) != 1 or "cr-615-prevention-effects" not in mechanic_ids
            or target_schema != {"zones": ["player", "battlefield"], "categories": ["player", "permanent"],
                                 "predicate": "damageable", "count": 1}):
        return ()
    expected = _chosen_source_effect(
        shield={"op": "create_damage_prevention_shield", "source": "$source", "subject": "$target.0",
                "mode": "all", "duration": "until_end_of_turn"},
        qualifier=None,
    )
    return ("damage.prevention.persistent_amount", "target.revalidate_resolution",
            "target.public.player_or_damageable_permanent") if effects[0] == expected else ()


def paired_target_combat_prevention_capabilities(*, effects, target_schema, mechanic_ids):
    """Validate the complete ordered to/from pair sharing one stack group."""
    if (len(effects) != 2 or "cr-615-prevention-effects" not in mechanic_ids
            or target_schema is None or not direct_permanent_target_schema_is_closed(target_schema)):
        return ()
    base = {
        "op": "create_damage_prevention_shield",
        "source": "$source",
        "subject": "*",
        "mode": "all",
        "duration": "until_end_of_turn",
        "damage_kind": "combat",
        "application_group_id": "$stack",
    }
    expected = (
        {**base, "scope": DamagePreventionScope(target_ref="$target.0").to_dict()},
        {**base, "scope": DamagePreventionScope(source_ref="$target.0").to_dict()},
    )
    if tuple(thaw_value(effect) for effect in effects) != expected:
        return ()
    return ("damage.prevention.persistent_amount", "target.revalidate_resolution")


def fixed_prevention_component_capabilities(*,effects,target_schema,mechanic_ids):
    if 'cr-615-prevention-effects' not in set(mechanic_ids):return ()
    if len(effects)==2 and target_schema is None:
        from ..source_prevention import SOURCE_SUBJECT_VERSION,SOURCE_COMBAT_DIRECTION,source_prevention_dependencies
        if (all(isinstance(effect,Mapping) and effect.get(SOURCE_SUBJECT_VERSION)==3 for effect in effects)
                and tuple(effect.get(SOURCE_COMBAT_DIRECTION) for effect in effects)==('to','from')):
            try:return tuple(sorted({cap for effect in effects for cap in source_prevention_dependencies(effect)}))
            except (TypeError,ValueError):return ()
    if len(effects)!=1:return ()
    effect=effects[0]
    if _has_combat_token_aftermath(effect):
        return _combat_token_aftermath_capabilities(effects, target_schema)
    if isinstance(effect,Mapping) and {'source_controller_target_version','source_controller_target'}.intersection(effect):
        from .target_controller_prevention import targeted_controller_prevention_capabilities
        return targeted_controller_prevention_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
    from ..source_prevention import (
        EXCLUDED_SOURCE_TARGET_FIELDS, source_prevention_dependencies, targeted_source_exclusion_schema_is_closed,
    )
    if isinstance(effect, Mapping) and EXCLUDED_SOURCE_TARGET_FIELDS.intersection(effect):
        if not targeted_source_exclusion_schema_is_closed(target_schema):
            return ()
        try:
            return source_prevention_dependencies(effect)
        except (TypeError, ValueError):
            return ()
    if isinstance(effect, Mapping) and 'source_subject_version' in effect:
        if target_schema is not None:return ()
        try:return source_prevention_dependencies(effect)
        except (TypeError, ValueError):return ()
    direct_fields={'op','source','subject','mode','duration'}
    # The ordinary global and resolving-controller shields have no additional
    # scope descriptor. Their existing damage owner supplies that default.
    if (isinstance(effect,Mapping) and target_schema is None
            and set(effect) in (direct_fields,direct_fields|{'damage_kind'})
            and effect.get('op')=='create_damage_prevention_shield'
            and effect.get('source')=='$source' and effect.get('mode')=='all'
            and effect.get('duration')=='until_end_of_turn'
            and isinstance(effect.get('subject'),str) and effect['subject'] in {'*','$controller'}
            and isinstance(effect.get('damage_kind','any'),str)
            and effect.get('damage_kind','any') in {'any','combat','noncombat'}):
        return ('damage.prevention.persistent_amount',)
    if (isinstance(effect,Mapping) and effect.get('op')=='create_damage_prevention_shield'
            and effect.get('source')=='$source' and effect.get('mode')=='all'
            and effect.get('duration')=='until_end_of_turn' and target_schema is not None
            and ((set(effect)==direct_fields and effect.get('subject')=='$target.0')
                or (set(effect)==direct_fields|{'chosen_source','source_predicate'} and effect.get('subject')=='*' and effect.get('chosen_source')=='$target.0'))):
        if direct_permanent_target_schema_is_closed(target_schema):
            from .permanent_predicate_capability_shapes import direct_target_predicate_capabilities
            return ('damage.prevention.persistent_amount','target.revalidate_resolution',*direct_target_predicate_capabilities(target_schema))
        if target_schema=={'zones':['stack'],'categories':['spell'],'source_exclusion':True,'count':1}:
            return ('damage.prevention.persistent_amount','target.revalidate_resolution')
    from ..designation_prevention import DESIGNATION_PREDICATE_KIND, DESIGNATION_SCOPE_KIND, designation_predicate_dependencies
    if isinstance(effect,Mapping) and effect.get('op')=='create_damage_prevention_shield' and isinstance(effect.get('scope'),Mapping) and effect['scope'].get('kind')==DESIGNATION_SCOPE_KIND:
        try:own=designation_predicate_dependencies(effect['scope'])
        except (ValueError,TypeError):return ()
        if (set(effect)!={'op','source','subject','mode','duration','scope'} or target_schema is not None
                or effect.get('source')!='$source' or effect.get('subject')!='$controller'
                or effect.get('mode')!='next_instance' or effect.get('duration')!='until_end_of_turn'):return ()
        return ('damage.prevention.persistent_amount','zone.entry.public_designation',*own)
    if isinstance(effect,Mapping) and effect.get('op')=='choose_damage_source' and isinstance(effect.get('source_predicate'),Mapping) and effect['source_predicate'].get('kind')==DESIGNATION_PREDICATE_KIND:
        try:own=designation_predicate_dependencies(effect['source_predicate'])
        except (ValueError,TypeError):return ()
        if (set(effect)!={'op','prompt','source_predicate','shield'} or target_schema is not None
                or effect['shield']!={'op':'create_damage_prevention_shield','source':'$source','subject':'$controller','mode':'next_instance','duration':'until_end_of_turn'}):return ()
        return ('damage.prevention.persistent_amount','zone.entry.public_designation',*own)
    from ..attached_prevention_model import attached_prevention_dependencies
    attached=effect.get('shield') if isinstance(effect,Mapping) and effect.get('op')=='choose_damage_source' else effect
    try:attachment_dependencies=attached_prevention_dependencies(attached)
    except (TypeError,ValueError):return ()
    if attachment_dependencies:
        if target_schema is not None:return ()
        if effect is not attached:
            if set(effect)!={'op','prompt','source_predicate','shield'}:return ()
            from ..object_query import ObjectQuerySpec,validate_chosen_damage_source_predicate
            try:validate_chosen_damage_source_predicate(ObjectQuerySpec.from_dict(effect['source_predicate']))
            except (TypeError,ValueError):return ()
        return ('damage.prevention.persistent_amount',*attachment_dependencies)
    from ..prevention_recipient_sets import prevention_recipient_set_dependencies
    if (isinstance(effect, Mapping) and set(effect)=={'op','source','subject','mode','duration','amount','selector'}
            and effect.get('source')=='$source' and effect.get('subject')=='*'
            and effect.get('mode')=='amount' and effect.get('duration')=='until_end_of_turn'
            and type(effect.get('amount')) is int and effect['amount']>0 and target_schema is None):
        try:own=prevention_recipient_set_dependencies(effect)
        except (ValueError,TypeError):return ()
        if own:return ('damage.prevention.persistent_amount',*own)
    if (not isinstance(effect,Mapping) or set(effect)!={'op','source','subject','mode','duration','damage_kind','scope'}
            or effect.get('op')!='create_damage_prevention_shield' or effect.get('source')!='$source'
            or effect.get('subject')!='*' or effect.get('mode')!='all'
            or effect.get('duration')!='until_end_of_turn' or effect.get('damage_kind') not in {'any','combat','noncombat'}):return ()
    try:scope=DamagePreventionScope.from_dict(effect['scope'])
    except (ValueError,TypeError,KeyError):return ()
    if scope.source_power_max is not None and effect["damage_kind"] != "combat":
        return ()
    references=(scope.source_ref,scope.target_ref,scope.excluded_source_ref,scope.excluded_target_ref)
    if any(value not in {None,'$source','$target.0'} for value in references):return ()
    targeted='$target.0' in references
    if targeted != (target_schema is not None):return ()
    if targeted and not direct_permanent_target_schema_is_closed(target_schema):return ()
    return ('damage.prevention.persistent_amount','target.revalidate_resolution') if targeted else ('damage.prevention.persistent_amount',)
