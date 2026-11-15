"""A fixed Awaken price and its correlated ordinary and augmented spell bodies."""
from collections.abc import Mapping
from dataclasses import dataclass
from .replacement.immutable import FrozenMap
from .spell_text_variants import SpellTextVariantKind,validated_variant_mana


AWAKEN_KIND=SpellTextVariantKind.AWAKEN
AWAKEN_CAPABILITY=AWAKEN_KIND.capability
AWAKEN_MECHANIC='awaken'
AWAKEN_PAID_MECHANIC='fixed-awaken-paid-body'


@dataclass(frozen=True,slots=True)
class FixedManaAwakenSpec:
    instance_id: str
    requirements: FrozenMap
    count: int
    normal_targeted: bool
    normal_fingerprint: str
    awakened_fingerprint: str
    normal_mechanics: tuple[str,...]
    schema_version: int=1

    def __post_init__(self):
        object.__setattr__(self,'requirements',validated_variant_mana(self.instance_id,self.requirements,
            self.normal_fingerprint,self.awakened_fingerprint,self.schema_version))
        if type(self.count)is not int or self.count<=0 or type(self.normal_targeted)is not bool:
            raise ValueError('Awaken requires a positive fixed count and a strict ordinary-target flag')
        if (not isinstance(self.normal_mechanics,(list,tuple)) or not self.normal_mechanics
                or any(type(value)is not str or not value for value in self.normal_mechanics)
                or len(self.normal_mechanics)!=len(set(self.normal_mechanics))):
            raise ValueError('Awaken requires the original closed body mechanics')
        object.__setattr__(self,'normal_mechanics',tuple(self.normal_mechanics))

    def to_dict(self):
        return {'schema_version':self.schema_version,'instance_id':self.instance_id,'requirements':dict(self.requirements),
                'count':self.count,'normal_targeted':self.normal_targeted,'normal_fingerprint':self.normal_fingerprint,
                'awakened_fingerprint':self.awakened_fingerprint,'normal_mechanics':list(self.normal_mechanics)}

    @classmethod
    def from_dict(cls,value):
        fields={'schema_version','instance_id','requirements','count','normal_targeted','normal_fingerprint','awakened_fingerprint','normal_mechanics'}
        if not isinstance(value,Mapping) or set(value)!=fields:
            raise ValueError('Awaken declaration fields are closed')
        return cls(**dict(value))
