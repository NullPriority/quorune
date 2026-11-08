from __future__ import annotations

"""Closed temporary Defender permission using the canonical duration journal."""

from dataclasses import replace
from typing import Mapping
from ..defender_permission import (
    DefenderAttackPermission, DEFENDER_PERMISSION_CAPABILITY,
    DEFENDER_RULE_CAPABILITY, DEFENDER_RULE_VERSION,
    DEFENDER_TEMPORARY_CAPABILITY, DEFENDER_TEMPORARY_MECHANIC, DEFENDER_TEMPORARY_OPERATION,
)
from ..ability_fragments import ability_fragment_to_dict
from ..continuous_effects import ContinuousOperation, Layer
from ..continuous_effect_state import (
    ResolutionContinuousComponent, create_resolution_continuous_effect_components,
    create_resolution_declaration_rule_effect,
    matching_battlefield_objects, resolution_effect_source,
)
from ..object_predicate import ObjectQuerySpec
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS, FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..errors import GameRuleError

def decode_temporary_defender_permission(effect):
    required={'op','schema_version','permission','power','toughness','keywords'}
    fields=set(effect)-{'_runtime_source'}
    if fields not in (required|{'card'},required|{'predicate'}):raise ValueError('Temporary Defender permission has a closed schema')
    version = effect['schema_version']
    if effect['op']!=DEFENDER_TEMPORARY_OPERATION or type(version) is not int or version not in {3, DEFENDER_RULE_VERSION}:
        raise ValueError('Temporary Defender permission version is unsupported')
    if effect['permission']!='ignore_defender' or type(effect['power']) is not int or type(effect['toughness']) is not int:
        raise ValueError('Temporary Defender permission modifier is malformed')
    keywords=effect['keywords']
    if not isinstance(keywords,(list,tuple)) or any(type(k) is not str or k not in FIXED_CHARACTERISTIC_KEYWORDS for k in keywords) or len(set(keywords))!=len(keywords):
        raise ValueError('Temporary Defender permission keywords are unsupported')
    selection=ObjectQuerySpec.from_dict(effect['predicate']) if 'predicate' in effect else effect['card']
    if version == DEFENDER_RULE_VERSION:
        if isinstance(selection, ObjectQuerySpec):
            if (selection.exclude_ref is not None or selection.keywords_all not in {(), ('defender',)}
                    or selection.keywords_all and (effect['power'] or effect['toughness'] or keywords)):
                raise ValueError('Live Defender qualifiers require a rule-only public group')
        elif type(selection) is not str or not selection:
            raise ValueError('Locked Defender permission needs one battlefield reference')
    return selection,tuple(keywords)


def temporary_defender_permission_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects)!=1 or DEFENDER_TEMPORARY_MECHANIC not in mechanic_ids:return ()
    try:selection,keywords=decode_temporary_defender_permission(effects[0])
    except (KeyError,TypeError,ValueError):return ()
    dependencies={DEFENDER_PERMISSION_CAPABILITY,DEFENDER_TEMPORARY_CAPABILITY,
        'continuous.resolution.fixed_characteristics_until_end_of_turn'}
    resolved_rule = effects[0]['schema_version'] == DEFENDER_RULE_VERSION
    if resolved_rule:
        dependencies.add(DEFENDER_RULE_CAPABILITY)
    if isinstance(selection,ObjectQuerySpec):
        from ..compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
        shape_query = replace(selection, keywords_all=()) if resolved_rule else selection
        if 'creature' not in selection.types_all or not fixed_resolution_characteristic_query_is_closed(shape_query,target_schema=target_schema):return ()
    elif selection=='$source.zone_object':
        if target_schema is not None:return ()
    elif selection=='$target.0':
        from .node_capability_shapes import direct_target_predicate_capabilities
        from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed
        if not direct_permanent_target_schema_is_closed(target_schema) or target_schema.get('types_any',target_schema.get('types_all'))!=['creature']:return ()
        dependencies.update(direct_target_predicate_capabilities(target_schema));dependencies.add('target.revalidate_resolution')
    else:return ()
    dependencies.update(cap for keyword in keywords for cap in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword])
    return tuple(sorted(dependencies))


def temporary_defender_permission_runtime_dependencies(program):
    """Infer temporary Defender authority from the executable descriptor."""
    required: set[str] = set()

    def visit(value):
        if isinstance(value, Mapping):
            if (value.get("op") == DEFENDER_TEMPORARY_OPERATION
                    and value.get("schema_version") in (3, DEFENDER_RULE_VERSION)):
                predicate = value.get("predicate")
                targeted = value.get("card") == "$target.0" or (
                    isinstance(predicate, Mapping) and predicate.get("controller") == "$target.0"
                )
                owners = temporary_defender_permission_capabilities(
                    effects=(value,), target_schema=program.target_schema if targeted else None,
                    mechanic_ids=(DEFENDER_TEMPORARY_MECHANIC,),
                )
                if not owners:
                    raise ValueError("Temporary Defender permission lacks complete rule closure")
                required.update(owners)
                return
            for child in value.values():
                visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value:
                visit(child)

    visit(program.effects)
    return tuple(sorted(required))


def apply_temporary_defender_permission(host,effect,*,actor,reason):
    try:selection,keywords=decode_temporary_defender_permission(effect)
    except (KeyError,TypeError,ValueError) as exc:raise GameRuleError(str(exc)) from exc
    resolved_rule = effect['schema_version'] == DEFENDER_RULE_VERSION
    live_query = selection if resolved_rule and isinstance(selection, ObjectQuerySpec) else None
    if isinstance(selection,ObjectQuerySpec):
        affected = (
            matching_battlefield_objects(host, selection)
            if live_query is None or effect['power'] or effect['toughness'] or keywords
            else ()
        )
    else:
        try:affected=(host._resolve_object(actor,selection,zones={'battlefield'}),)
        except GameRuleError:return ()
    permission = DefenderAttackPermission()
    ability_operations = tuple(ContinuousOperation('add_ability', keyword) for keyword in keywords)
    if not resolved_rule:
        affected=tuple(card for card in affected if 'creature' in host._type_parts(str(host._effective_card_data(card).get('type_line') or ''))[0])
        ability_operations = (
            ContinuousOperation('add_ability_fragment', ability_fragment_to_dict(permission)),
            *ability_operations,
        )
    components = []
    if ability_operations:
        components.append(ResolutionContinuousComponent(layer=Layer.ABILITY, sublayer='6', operations=ability_operations))
    if effect['power'] or effect['toughness']:
        components.append(ResolutionContinuousComponent(layer=Layer.POWER_TOUGHNESS,sublayer='7c',
            operations=(ContinuousOperation('modify_power_toughness',[effect['power'],effect['toughness']]),)))
    source = resolution_effect_source(host, effect, fallback_card=affected[0] if affected else None)
    if components:
        create_resolution_continuous_effect_components(host,source=source,targets=affected,components=tuple(components))
    if resolved_rule:
        create_resolution_declaration_rule_effect(
            host, source=source, targets=affected if live_query is None else (),
            restriction=permission, live_query=live_query,
        )
    receivers = 'matching' if live_query is not None else 'selected'
    host._log(actor,'permanent.defender_permission',f'{reason}: {receivers} creatures can attack with Defender this turn.',
        {'objects':[card.ref for card in affected],'reason':reason},importance=1,changed_objects=[card.object_id for card in affected])
    return tuple(card.ref for card in affected)
