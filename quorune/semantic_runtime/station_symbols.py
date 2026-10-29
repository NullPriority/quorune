"""Station symbols emit ordinary source-bound type, ability and P/T layers."""
from dataclasses import dataclass
from ..station_symbol_model import STATION_SYMBOL_CAPABILITY, STATION_SYMBOL_HANDLER, StationSymbolsSpec
from ..ability_fragments import StaticComponentSpec, ability_fragment_to_dict
from ..continuous_effect_model import ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectRelation, ContinuousObjectIdentity, ContinuousOperation, Layer
from ..object_predicate import ObjectQuerySpec
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class StationSymbolsHandler:
    handler_id: str = STATION_SYMBOL_HANDLER
    schema_version: int = 1
    family: str = STATION_SYMBOL_CAPABILITY
    event: str = 'characteristics.evaluate'
    rule_references: tuple[str, ...] = ('721.2', '721.2a', '721.2b', '721.2c', '613.1d', '613.1f', '613.4b', '613.6')
    capability_dependencies: tuple[str, ...] = (STATION_SYMBOL_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'symbols'}, field='Station symbols')
        if descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int or descriptor['schema_version'] != 1 or descriptor['event'] != self.event:
            raise SemanticNodeError('Station symbol identity or version is malformed')
        try:
            spec = StationSymbolsSpec.from_dict({'symbols': descriptor['symbols']})
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if any(keyword.casefold() not in {value.casefold() for value in FIXED_CHARACTERISTIC_KEYWORDS} for symbol in spec.symbols for keyword in symbol.keywords):
            raise SemanticNodeError('Station symbol keywords require represented rules')
        return spec

    def descriptor_source_component_layer(self, descriptor):
        return Layer.TYPE if any(symbol.power is not None for symbol in self.validate(descriptor).symbols) else Layer.ABILITY

    def lower(self, descriptor, context):
        spec = self.validate(descriptor)
        if not context.source_logical_object_id:
            raise SemanticNodeError('Station symbols require the original source incarnation')
        try:
            active = tuple(symbol for symbol in spec.symbols if symbol.active(context.source_counters))
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        common = {'source_id': context.source_object_id, 'timestamp': context.source_timestamp,
                  'origin': ContinuousEffectOrigin.STATIC_ABILITY, 'applies': ObjectQuerySpec(zones=('battlefield',)),
                  'relation': ContinuousEffectRelation.SOURCE_OBJECT,
                  'related_object': ContinuousObjectIdentity(context.source_object_id, context.source_logical_object_id)}
        effects = []
        def effect(label, layer, sublayer, operations):
            if operations:
                effects.append(ContinuousEffect(effect_id=f'{context.source_object_id}:{context.component_id}:station-{label}',
                    layer=layer, sublayer=sublayer, operations=tuple(operations), **common))
        creature = next((symbol for symbol in active if symbol.power is not None), None)
        if creature is not None:
            effect('type', Layer.TYPE, '4', (ContinuousOperation('add_types', ['Creature'], field='card_types'),))
        effect('abilities', Layer.ABILITY, '6', (
            *(ContinuousOperation('add_ability', keyword) for symbol in active for keyword in symbol.keywords),
            *(ContinuousOperation('remove_ability_fragment', ability_fragment_to_dict(StaticComponentSpec(key)))
              for symbol in spec.symbols if symbol not in active for key in symbol.semantic_keys)))
        if creature is not None:
            effect('power-toughness', Layer.POWER_TOUGHNESS, '7b',
                   (ContinuousOperation('set_power_toughness', [creature.power, creature.toughness]),))
        return tuple(effects)
