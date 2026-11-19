"""A closed optional permanent payment against the original cast spell."""
from collections.abc import Mapping
from dataclasses import dataclass
from .fixed_effect_payment import FixedEffectPaymentSpec
from .object_predicate import ObjectQuerySpec

CAST_SOURCE_SACRIFICE_PAYMENT_CAPABILITY = 'choice.payment.cast_source_sacrifice'
CAST_SOURCE_SACRIFICE_WRAPPER_VERSION = 6
_DOMAINS = ('artifact','battle','creature','enchantment','land','planeswalker')


@dataclass(frozen=True,slots=True)
class CastSourceSacrificePaymentSpec:
    permanent_type: str
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1 or self.permanent_type not in _DOMAINS:
            raise ValueError('Cast-source payment requires one fixed public permanent type')

    def payment(self,actor='$controller'):
        return FixedEffectPaymentSpec('sacrifice',schema_version=2,
            predicate=ObjectQuerySpec(zones=('battlefield',),controller=actor,types_all=(self.permanent_type,)))

    def effect(self):
        return {'op':'offer_optional_mana_payment','schema_version':CAST_SOURCE_SACRIFICE_WRAPPER_VERSION,
            'player':'$controller','source_stack':'$context.stack','payment':self.payment().to_dict(),
            'effects':[],'declined_effects':[{'op':'counter_stack_target','stack':'$context.stack'}]}

    @property
    def capabilities(self):
        return tuple(sorted({CAST_SOURCE_SACRIFICE_PAYMENT_CAPABILITY,'stack.counter.effect',
            'trigger.event.normalized_spell_cast',*self.payment().capabilities}))


def cast_source_sacrifice_spec(effect,*,actor='$controller'):
    fields={'op','schema_version','player','source_stack','payment','effects','declined_effects'}
    if (not isinstance(effect,Mapping) or set(effect)!=fields or effect.get('op')!='offer_optional_mana_payment'
            or type(effect.get('schema_version'))is not int or effect['schema_version']!=CAST_SOURCE_SACRIFICE_WRAPPER_VERSION
            or effect['player']!=actor or not isinstance(effect['effects'],(list,tuple)) or tuple(effect['effects'])!=()):
        raise ValueError('Cast-source sacrifice wrapper fields and paid branch are closed')
    ref=effect['source_stack']
    if type(ref)is not str or not ref or actor=='$controller' and ref!='$context.stack':
        raise ValueError('Cast-source sacrifice must retain the sealed original stack reference')
    if not isinstance(effect['declined_effects'],(list,tuple)) or tuple(effect['declined_effects'])!=({'op':'counter_stack_target','stack':ref},):
        raise ValueError('Declining a cast-source payment counters only its original spell')
    payment=FixedEffectPaymentSpec.from_dict(effect['payment'])
    types=payment.predicate.types_all if payment.predicate is not None else ()
    if len(types)!=1 or types[0] not in _DOMAINS:raise ValueError('Cast-source payment type is not represented')
    spec=CastSourceSacrificePaymentSpec(types[0])
    if payment!=spec.payment(actor):raise ValueError('Cast-source payment count, ownership or predicate changed')
    return spec


def cast_source_sacrifice_runtime_dependencies(program,effect):
    spec=cast_source_sacrifice_spec(effect)
    def own_cast(condition):
        return isinstance(condition,Mapping) and (condition=={'field':'card','op':'eq','value':'$source.ref'}
            or any(own_cast(c) for c in condition.get('all',())))
    if (program.event!='spell.cast' or program.active_zone!='stack' or program.target_schema is not None
            or not own_cast(program.event_condition)):
        raise ValueError('Cast-source payment requires the original spell own-cast occurrence')
    return spec.capabilities
