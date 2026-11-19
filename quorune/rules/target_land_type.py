"""Validate a chosen land instruction and delegate its layer-4 journal entry."""
from ..target_land_type_model import (
    TARGET_LAND_TYPE_OPERATION,TARGET_LAND_TYPE_CAPABILITY,TARGET_LAND_TYPE_MECHANIC,
    decode_target_land_type,target_land_type_schema_is_exact,TargetLandTypeSettingIntent,
    LIMITED_TARGET_LAND_TYPE_CAPABILITY,
)
from ..basic_type_setting_model import BASIC_TYPE_SETTING_CAPABILITY

_TARGET_LAND_TYPE_CAPABILITIES=(
    TARGET_LAND_TYPE_CAPABILITY,BASIC_TYPE_SETTING_CAPABILITY,
    'continuous.resolution.fixed_characteristics_until_end_of_turn','target.revalidate_resolution',
)


def _choice_capabilities(effect):
    return (*_TARGET_LAND_TYPE_CAPABILITIES, LIMITED_TARGET_LAND_TYPE_CAPABILITY) if (
        effect['schema_version'] == 3
    ) else _TARGET_LAND_TYPE_CAPABILITIES


def target_land_type_node_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects)!=1 or not {TARGET_LAND_TYPE_MECHANIC,'cr-115-targets'} <= set(mechanic_ids):
        return ()
    try:decode_target_land_type(effects[0])
    except (TypeError,ValueError):return ()
    if not target_land_type_schema_is_exact(target_schema,effects[0]['schema_version']==2):return ()
    return _choice_capabilities(effects[0])


def target_land_type_runtime_dependencies(program):
    from collections.abc import Mapping
    required=set()
    def visit(value):
        if isinstance(value,Mapping):
            if value.get('op')==TARGET_LAND_TYPE_OPERATION:
                decode_target_land_type(value)
                if not target_land_type_schema_is_exact(program.target_schema,value['schema_version']==2):
                    raise ValueError('Basic type choice lost its one current land target')
                required.update(_choice_capabilities(value))
            for child in value.values():visit(child)
        elif isinstance(value,(tuple,list)):
            for child in value:visit(child)
    visit((program.effects,program.handlers));return tuple(sorted(required))


def is_closed_target_land_type_program(program):
    required=target_land_type_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)


def apply_target_land_type(host,intent):
    from ..errors import GameRuleError
    from ..continuous_effect_state import ResolutionEffectSource,create_resolution_continuous_effect
    from ..continuous_effect_model import Layer,ContinuousOperation
    if not isinstance(intent,TargetLandTypeSettingIntent):
        raise GameRuleError('Basic land type setting needs its typed intent')
    if intent.actor not in host.active_seats:
        raise GameRuleError('The resolving basic type chooser is unavailable')
    card=host.state.cards.get(intent.object_id)
    if (card is None or card.ref!=intent.card_ref or card.logical_object_id!=intent.logical_object_id
            or card.zone!='battlefield' or card.phased_out or 'land' not in host._effective_type_parts(card)[0]):
        raise GameRuleError('The original land type recipient is unavailable')
    if intent.required_controller is not None and card.controller!=intent.required_controller:
        raise GameRuleError('The original land is no longer controlled by the resolving player')
    source=ResolutionEffectSource(intent.source_stack_ref,intent.source_object_id,
        intent.source_logical_object_id,intent.source_card_ref)
    effect=create_resolution_continuous_effect(host,source=source,targets=(card,),layer=Layer.TYPE,sublayer='4',
        operations=(ContinuousOperation('set_basic_land_types',[intent.basic_type]),))
    if effect is None:raise GameRuleError('Basic land type setting requires the continuous journal')
    return effect
