"""Versioned private colored-hand selection and fixed result parameterization."""
from collections.abc import Mapping
from dataclasses import dataclass
from .replacement.immutable import FrozenMap, thaw_value

COLORED_REVEAL_OPERATION = 'reveal_colored_hand_then_effect'
COLORED_REVEAL_CAPABILITY = 'hand.reveal.colored_count_result'
COLORED_REVEAL_MECHANIC = 'colored-hand-reveal-results'
REVEAL_RESULT_KIND = 'colored_hand_result_template'
REVEAL_COUNT_KIND = 'selected_hand_reveal_count'
_SLOTS = {
    'damage': {('amount',)}, 'life': {('delta',)},
    'modify_stats_until_end_of_turn': {('power',), ('toughness',)},
    'counter_unless_pay': {('cost', 'GENERIC')},
}


def reveal_count(coefficient=1):
    if type(coefficient) is not int or coefficient not in {-3,-2,-1,1,2,3}:
        raise ValueError('Reveal result requires a bounded signed coefficient')
    return {'kind':REVEAL_COUNT_KIND,'schema_version':1,'coefficient':coefficient}


def _materialize(value,count,*,operation,path=()):
    if isinstance(value,Mapping):
        if value.get('kind')==REVEAL_COUNT_KIND:
            if (set(value)!={'kind','schema_version','coefficient'}
                    or type(value['schema_version']) is not int or value['schema_version']!=1
                    or path not in _SLOTS.get(operation,set())):
                raise ValueError('Reveal quantity is outside its owned result slot')
            return reveal_count(value['coefficient'])['coefficient']*count,1
        result={};seen=0
        for key,child in value.items():
            result[key],used=_materialize(child,count,operation=operation,path=(*path,key));seen+=used
        return result,seen
    if isinstance(value,(list,tuple)):
        raise ValueError('Reveal result does not accept nested or variable programs')
    return value,0


@dataclass(frozen=True,slots=True)
class ColoredRevealResult:
    effect: FrozenMap
    target_schema: FrozenMap | None
    schema_version: int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1 or not isinstance(self.effect,Mapping):
            raise ValueError('Unsupported colored reveal result version')
        object.__setattr__(self,'effect',FrozenMap(self.effect))
        if self.target_schema is not None:
            if (not isinstance(self.target_schema,Mapping) or type(self.target_schema.get('count')) is not int
                    or self.target_schema['count']!=1):raise ValueError('Reveal result requires one ordinary target')
            object.__setattr__(self,'target_schema',FrozenMap(self.target_schema))
        if self.effect.get('op') not in _SLOTS:raise ValueError('Unrepresented revealed-card result')
        _,count=_materialize(self.effect,2,operation=self.effect['op'])
        if not 1<=count<=2:raise ValueError('Reveal result needs one or two owned quantity slots')

    def materialize(self,count):
        if type(count) is not int or count<0:raise ValueError('Revealed-card count must be nonnegative')
        return _materialize(self.effect,count,operation=self.effect['op'])[0]

    def to_dict(self):
        return {'kind':REVEAL_RESULT_KIND,'schema_version':1,'effect':thaw_value(self.effect),
            'target_schema':thaw_value(self.target_schema) if self.target_schema is not None else None}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version','effect','target_schema'}
                or value['kind']!=REVEAL_RESULT_KIND):raise ValueError('Colored reveal result fields are closed')
        return cls(value['effect'],value['target_schema'],value['schema_version'])


@dataclass(frozen=True,slots=True)
class ColoredHandRevealSpec:
    color: str
    result: ColoredRevealResult
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or self.color not in tuple('WUBRG') or not isinstance(self.result,ColoredRevealResult)):
            raise ValueError('Colored hand reveal needs one color and one versioned result')

    def to_dict(self):return {'schema_version':1,'color':self.color,'result':self.result.to_dict()}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','color','result'}:
            raise ValueError('Colored hand reveal fields are closed')
        return cls(value['color'],ColoredRevealResult.from_dict(value['result']),value['schema_version'])

    def effect(self):return {'op':COLORED_REVEAL_OPERATION,'player':'$controller','reveal':self.to_dict()}


def decode_colored_hand_reveal(effect,*,player='$controller'):
    if (not isinstance(effect,Mapping) or set(effect)!={'op','player','reveal'}
            or effect['op']!=COLORED_REVEAL_OPERATION or effect['player']!=player):
        raise ValueError('Colored hand reveal requires its closed controller instruction')
    return ColoredHandRevealSpec.from_dict(effect['reveal'])
