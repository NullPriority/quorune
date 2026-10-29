from __future__ import annotations

"""Fixed characteristic effects selected by an authoritative player attachment."""

from dataclasses import dataclass
from ..enchanted_player_model import ENCHANTED_PLAYER_CHARACTERISTICS_CAPABILITY,ENCHANTED_PLAYER_CHARACTERISTICS_HANDLER
from ..continuous_effect_model import ContinuousEffectOrigin,Layer
from ..object_predicate import ObjectQuerySpec
from .component_registry import exact_fields
from .context import SemanticNodeError
from .fixed_characteristic_settings import fixed_setting_modifier_node,FIXED_SETTING_CAPABILITY
from .fixed_characteristic_effects import fixed_characteristic_effects


@dataclass(frozen=True,slots=True)
class EnchantedPlayerCharacteristicsHandler:
    handler_id:str=ENCHANTED_PLAYER_CHARACTERISTICS_HANDLER
    schema_version:int=1
    family:str=ENCHANTED_PLAYER_CHARACTERISTICS_CAPABILITY
    event:str='characteristics.evaluate'
    rule_references:tuple[str,...]=('303.4c','303.4e','611.3a','611.3b','611.3c','613.1d','613.1f','613.4b','613.4c')
    capability_dependencies:tuple[str,...]=(ENCHANTED_PLAYER_CHARACTERISTICS_CAPABILITY,FIXED_SETTING_CAPABILITY)

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','target','modifier'},field='Enchanted-player characteristics')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version']!=1 or descriptor['event']!=self.event):
            raise SemanticNodeError('Enchanted-player characteristic identity changed')
        exact_fields(descriptor['target'],{'nonlegendary'},field='Enchanted-player creature scope')
        if type(descriptor['target']['nonlegendary']) is not bool:
            raise SemanticNodeError('Enchanted-player creatures have a closed legendary exclusion')
        return descriptor['target']['nonlegendary'],fixed_setting_modifier_node(descriptor['modifier'])

    def descriptor_source_component_layer(self,descriptor):
        _nonlegendary,node=self.validate(descriptor)
        return Layer.TYPE if node.type_operations else Layer.COLOR if node.color_operations else Layer.ABILITY

    def lower(self,descriptor,context):
        nonlegendary,node=self.validate(descriptor)
        if context.enchanted_player is None:return ()
        predicate=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),controller=context.enchanted_player,
            excluded_supertypes=('legendary',) if nonlegendary else ())
        return fixed_characteristic_effects(node,context,common={'source_id':context.source_object_id,
            'timestamp':context.source_timestamp,'origin':ContinuousEffectOrigin.STATIC_ABILITY,'applies':predicate})
