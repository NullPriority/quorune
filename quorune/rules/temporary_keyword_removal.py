from __future__ import annotations

"""Fixed temporary removals use existing incarnation-locked layer commits."""

from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec,object_query_capabilities
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..next_turn_effect import NEXT_TURN_EFFECT_FIELD, next_turn_effect_dependencies, split_next_turn_effect

OPERATION='apply_source_characteristics_until_end_of_turn'
MECHANIC='fixed-temporary-keyword-removal'
CAPABILITY='continuous.resolution.fixed_keyword_removal'
OPPONENT_PERMANENT_KEYWORD_REMOVAL_CAPABILITY = 'continuous.resolution.opponent_permanent_keyword_removal'
_OPPONENT_PERMANENT_REMOVALS = ('Hexproof', 'Indestructible')
PROTECTION_FROM_BLACK='Protection from Black'
ALL_LANDWALK_ABILITIES='All Landwalk Abilities'
_TYPED_REMOVAL_CAPABILITIES = {
    PROTECTION_FROM_BLACK: ('protection.typed.debt',),
    ALL_LANDWALK_ABILITIES: ('combat.block.landwalk.basic_type', 'combat.block.landwalk.qualified_type'),
}
CHOICE_FIELD='target_keyword_removal_choice_version'
CHOICE_PROMPT='Choose a keyword to remove.'
CHOICE_OPTION_SETS=(('Flying','First Strike','Trample'), ('First Strike','Swampwalk'))


def opponent_permanent_keyword_removal_query(*, actor="$controller"):
    """The current battlefield permanents controlled by opponents."""
    return ObjectQuerySpec(zones=("battlefield",), excluded_controllers=(actor,))


def _is_opponent_permanent_keyword_removal(effect, query, *, actor="$controller"):
    return (
        query == opponent_permanent_keyword_removal_query(actor=actor)
        and tuple(effect["remove_keywords"]) == _OPPONENT_PERMANENT_REMOVALS
        and not effect["keywords"] and effect["power"] == effect["toughness"] == 0
        and NEXT_TURN_EFFECT_FIELD not in effect
    )


def temporary_keyword_removal_instruction(effect):
    fields={'op','schema_version','power','toughness','remove_keywords','keywords'}
    if not isinstance(effect, Mapping):
        raise ValueError('Temporary keyword removal has a closed selection/result schema')
    effect, next_turn = split_next_turn_effect(effect)
    if set(effect) not in (fields|{'card'},fields|{'predicate'}):
        raise ValueError('Temporary keyword removal has a closed selection/result schema')
    if effect['op']!=OPERATION or type(effect['schema_version']) is not int or effect['schema_version']!=11:
        raise ValueError('Temporary keyword removal requires its explicit version')
    if any(type(effect[field]) is not int for field in ('power','toughness')):
        raise ValueError('Temporary removal stat modifiers must be fixed integers')
    for field in ('remove_keywords','keywords'):
        values=effect[field]
        if (not isinstance(values,(list,tuple))
                or any(type(value) is not str or (
                    value not in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
                    and not (field == 'remove_keywords' and value in _TYPED_REMOVAL_CAPABILITIES)
                ) for value in values)
                or len(values)!=len(set(values))):
            raise ValueError('Temporary removal keywords require represented fixed abilities')
    if not effect['remove_keywords'] or set(effect['remove_keywords']).intersection(effect['keywords']):
        raise ValueError('Temporary removal requires a nonconflicting removal result')
    if set(effect['remove_keywords']).intersection(_TYPED_REMOVAL_CAPABILITIES) and (
        len(effect['remove_keywords']) != 1 or 'card' not in effect
        or effect['power'] or effect['toughness'] or effect['keywords'] or next_turn
    ):
        raise ValueError('Typed temporary removal requires a single fixed removal and no companion modifier')
    if 'card' in effect:
        if type(effect['card']) is not str or not effect['card']:
            raise ValueError('Temporary removal requires its selected incarnation reference')
        return effect['card']
    query=ObjectQuerySpec.from_dict(effect['predicate'])
    if query.to_dict()!=dict(effect['predicate']):
        raise ValueError('Temporary removal query is not canonical')
    return query


def damage_then_fixed_keyword_removal_capabilities(*, effects, target_schema, mechanic_ids):
    """Close source damage followed by one fixed removal on its announced target."""
    from ..compiler.bound_effect_programs import BOUND_EFFECT_PROGRAM_MECHANIC, CLOSED_EFFECT_PROGRAM_MECHANIC
    from ..compiler.direct_target import DirectPermanentTargetSpec
    from ..replacement.immutable import thaw_value
    from .damage_capability_shapes import fixed_damage_node_capabilities

    mechanics = set(mechanic_ids)
    if (
        len(effects) != 2
        or not {MECHANIC, 'cr-611-continuous-effects', BOUND_EFFECT_PROGRAM_MECHANIC,
                CLOSED_EFFECT_PROGRAM_MECHANIC} <= mechanics
        or not isinstance(target_schema, Mapping)
        or type(target_schema.get('count')) is not int
    ):
        return ()
    damage, removal = effects
    if not isinstance(damage, Mapping) or set(damage) != {'op', 'source', 'target', 'amount'}:
        return ()
    try:
        selection = temporary_keyword_removal_instruction(removal)
    except (TypeError, ValueError, KeyError):
        return ()
    removed = tuple(removal['remove_keywords'])
    if (
        selection != '$target.0' or len(removed) != 1 or removal['power'] or removal['toughness']
        or removal['keywords'] or NEXT_TURN_EFFECT_FIELD in removal
    ):
        return ()

    keyword, = removed
    target = {
        'Flying': DirectPermanentTargetSpec(types_all=('creature',), keywords_all=('flying',)),
        'Indestructible': DirectPermanentTargetSpec(types_any=('creature', 'planeswalker')),
    }.get(keyword)
    if target is None or keyword.casefold() not in mechanics or thaw_value(target_schema) != target.to_target_schema():
        return ()
    damage_dependencies = fixed_damage_node_capabilities(
        effects=(damage,), target_schema=target_schema, mechanic_ids=mechanics,
    )
    if not damage_dependencies:
        return ()
    return tuple(sorted({
        *damage_dependencies, CAPABILITY, 'continuous.resolution.fixed_characteristics_until_end_of_turn',
        *FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword],
    }))


def temporary_keyword_removal_capabilities(*,effects,target_schema,mechanic_ids):
    mechanics=set(mechanic_ids)
    if MECHANIC not in mechanics or 'cr-611-continuous-effects' not in mechanics:
        return ()
    damage_dependencies = ()
    if len(effects) == 2:
        from ..target_combat_damage_multiplier_model import turn_multiplier_instruction
        if effects[1] == turn_multiplier_instruction():
            from .loyalty_damage import turn_combat_multiplier_capabilities

            return turn_combat_multiplier_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
        ordered = damage_then_fixed_keyword_removal_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
        )
        if ordered:
            return ordered

        from ..damage_death_exile_model import DAMAGE_DEATH_EXILE_FIELD, DamageDeathExileScope
        from .damage_capability_shapes import fixed_damage_node_capabilities

        damage, removal = effects
        if (damage.get(DAMAGE_DEATH_EXILE_FIELD) != DamageDeathExileScope.TARGET.value
                or removal.get('card') != '$target.0'):
            return ()
        damage_dependencies = fixed_damage_node_capabilities(effects=(damage,), target_schema=target_schema,
                                                             mechanic_ids=mechanics)
        if not damage_dependencies:
            return ()
        effects = (removal,)
    if len(effects) != 1:
        return ()
    try:selection=temporary_keyword_removal_instruction(effects[0])
    except (ValueError,TypeError,KeyError):return ()
    dependencies={CAPABILITY,'continuous.resolution.fixed_characteristics_until_end_of_turn',
                  *damage_dependencies, *next_turn_effect_dependencies(effects[0])}
    if isinstance(selection,ObjectQuerySpec):
        from ..compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
        if _is_opponent_permanent_keyword_removal(effects[0], selection):
            if target_schema is not None or damage_dependencies:
                return ()
            dependencies.add(OPPONENT_PERMANENT_KEYWORD_REMOVAL_CAPABILITY)
        elif ('creature' not in selection.types_all
              or not fixed_resolution_characteristic_query_is_closed(selection, target_schema=target_schema)):
            return ()
        dependencies.update(object_query_capabilities(selection))
    elif selection=='$source.zone_object':
        if target_schema is not None:return ()
    elif selection=='$target.0':
        from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed,direct_target_predicate_capabilities
        if (not isinstance(target_schema,Mapping) or type(target_schema.get('count')) is not int
                or target_schema['count']!=1 or target_schema.get('types_any')!=['creature']
                or not direct_permanent_target_schema_is_closed(target_schema)):return ()
        dependencies.update(direct_target_predicate_capabilities(target_schema))
    else:return ()
    if target_schema is not None:
        if 'cr-115-targets' not in mechanics:return ()
        dependencies.add('target.revalidate_resolution')
    keywords=(*effects[0]['remove_keywords'],*effects[0]['keywords'])
    if len(keywords) == 1 and keywords[0] in _TYPED_REMOVAL_CAPABILITIES:
        from ..compiler.direct_target import direct_permanent_target_spec
        if (selection != '$target.0' or damage_dependencies
                or target_schema != direct_permanent_target_spec('target creature').to_target_schema()):
            return ()
        dependencies.update(_TYPED_REMOVAL_CAPABILITIES[keywords[0]])
        return tuple(sorted(dependencies))
    if not {keyword.casefold() for keyword in keywords}<=mechanics:return ()
    dependencies.update(cap for keyword in keywords for cap in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword])
    return tuple(sorted(dependencies))


def _keyword_removal_choice_marker(effect):
    if not isinstance(effect, Mapping):
        return False
    return CHOICE_FIELD in effect or (effect.get('op') == 'choose_option' and effect.get('prompt') == CHOICE_PROMPT)


def chosen_keyword_removal_capabilities(*, effects, target_schema, mechanic_ids):
    from ..compiler.direct_target import direct_permanent_target_spec
    from ..replacement.immutable import thaw_value

    if len(effects) != 1 or MECHANIC not in mechanic_ids:
        return ()
    creature = direct_permanent_target_spec('target creature').to_target_schema()
    if target_schema != creature or type(target_schema.get('count')) is not int:
        return ()
    choice = effects[0]
    fields = {'op', CHOICE_FIELD, 'player', 'prompt', 'options', 'then_by_choice'}
    if (not isinstance(choice, Mapping) or set(choice) != fields or choice['op'] != 'choose_option'
            or type(choice[CHOICE_FIELD]) is not int or choice[CHOICE_FIELD] != 1
            or choice['player'] != '$controller' or choice['prompt'] != CHOICE_PROMPT):
        return ()
    options, branches = choice['options'], choice['then_by_choice']
    if not isinstance(options, (list, tuple)) or not isinstance(branches, Mapping):
        return ()
    words = tuple(option.get('id') if isinstance(option, Mapping) else None for option in options)
    if words not in CHOICE_OPTION_SETS or set(branches) != set(words):
        return ()
    if tuple(options) != tuple({'id': word, 'label': word} for word in words):
        return ()
    dependencies = set()
    for word in words:
        branch = branches[word]
        expected = {'op': OPERATION, 'schema_version': 11, 'card': '$target.0',
            'power': 0, 'toughness': 0, 'remove_keywords': [word], 'keywords': []}
        if thaw_value(branch) != [expected]:
            return ()
        owned = temporary_keyword_removal_capabilities(
            effects=branch, target_schema=target_schema, mechanic_ids=mechanic_ids,
        )
        if not owned:
            return ()
        dependencies.update(owned)
    return tuple(sorted(dependencies))


def chosen_keyword_removal_runtime_dependencies(program):
    cost = program.cost_schema
    if ((program.event, program.active_zone) != ('activate', 'battlefield')
            or not isinstance(cost, Mapping) or cost.get('text') != '{T}' or cost.get('tap_source') is not True):
        raise ValueError('Chosen keyword removal requires its ordinary battlefield tap activation')
    required = chosen_keyword_removal_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError('Chosen keyword removal requires one closed target and one complete keyword choice')
    return required


def is_closed_chosen_keyword_removal_program(program):
    if not any(_keyword_removal_choice_marker(effect) for effect in program.effects):
        return False
    try:
        required = chosen_keyword_removal_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return set(required) <= set(program.capability_dependencies)


def temporary_keyword_removal_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if any(_keyword_removal_choice_marker(effect) for effect in nested_effects(program.effects)):
        return chosen_keyword_removal_runtime_dependencies(program)
    if not any(
        effect.get('op') == OPERATION
        and (effect.get('schema_version') == 11 or 'remove_keywords' in effect)
        for effect in program.effects
    ):
        return ()
    if any(effect.get('card')=='$event.permanent_object' for effect in program.effects):
        from .occurrence_object_results import occurrence_object_runtime_dependencies
        owned=occurrence_object_runtime_dependencies(program)
        if CAPABILITY not in owned:raise ValueError('Temporary removal lacks its sealed occurrence owner')
        return owned
    from ..compiler.program_composition import composed_spell_family_runtime_dependencies
    composed=composed_spell_family_runtime_dependencies(program,capability=CAPABILITY,mechanic=MECHANIC)
    if composed is not None:return composed
    dependencies=temporary_keyword_removal_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    if not dependencies:raise ValueError('Temporary keyword removal lacks complete selection/result closure')
    return dependencies


def apply_temporary_keyword_removal(host,effect,*,actor,reason):
    from ..continuous_effect_state import (ResolutionContinuousComponent,create_resolution_continuous_effect_components,
        matching_battlefield_objects,resolution_effect_source)
    from ..continuous_effect_model import ContinuousOperation,Layer
    from ..errors import GameRuleError
    instruction={key:value for key,value in effect.items() if key!='_runtime_source'}
    stale=instruction.get('card',True) is None
    if stale:instruction['card']='stale-resolved-reference'
    try:
        selection=temporary_keyword_removal_instruction(instruction)
        if isinstance(selection,ObjectQuerySpec):
            from .resolution_characteristic_shapes import fixed_resolution_characteristic_set_is_closed
            opponent_permanents = _is_opponent_permanent_keyword_removal(instruction, selection, actor=actor)
            if not opponent_permanents and not fixed_resolution_characteristic_set_is_closed(selection):
                raise ValueError('Temporary removal query is unavailable')
            affected=matching_battlefield_objects(host,selection)
        else:
            if stale:return ()
            try:affected=(host._resolve_object(actor,selection,zones={'battlefield'}),)
            except GameRuleError:return ()
        operations = []
        for keyword in instruction['remove_keywords']:
            if keyword == PROTECTION_FROM_BLACK:
                operation = ContinuousOperation('remove_color_protection', 'B')
            elif keyword == ALL_LANDWALK_ABILITIES:
                operation = ContinuousOperation('remove_landwalk_abilities')
            else:
                operation = ContinuousOperation('remove_ability', keyword)
            operations.append(operation)
        operations.extend(ContinuousOperation('add_ability', value) for value in instruction['keywords'])
        components=[ResolutionContinuousComponent(Layer.ABILITY,'6',tuple(operations))]
        if instruction['power'] or instruction['toughness']:
            components.append(ResolutionContinuousComponent(Layer.POWER_TOUGHNESS,'7c',(
                ContinuousOperation('modify_power_toughness',(instruction['power'],instruction['toughness'])),)))
        create_resolution_continuous_effect_components(host,source=resolution_effect_source(host,effect,fallback_card=affected[0] if affected else None),
            targets=affected, components=tuple(components),
            expires_on_next_turn_of=actor if NEXT_TURN_EFFECT_FIELD in instruction else None)
    except (ValueError,TypeError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
    refs=tuple(card.ref for card in affected)
    host._log(actor,'permanent.keyword_removal',reason,{'objects':list(refs),'reason':reason},importance=1,changed_objects=[card.object_id for card in affected])
    return refs
