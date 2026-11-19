"""Closed source-owned exceptions used only when declaring blockers."""
from collections.abc import Mapping
from dataclasses import dataclass
from .landwalk_permission_model import LandwalkBlockPermission

BLOCK_AS_THOUGH_CAPABILITY = 'combat.block.bounded_as_though'
BLOCK_AS_THOUGH_HANDLER = 'ability.static.bounded-block-as-though.v1'
BLOCK_AS_THOUGH_KIND = 'bounded_block_as_though'
_PAIRS = frozenset({
    ('self','attacker_without_shadow'), ('self','blocker_with_shadow'),
    ('self','reach_against_dragons'), ('attached_blocker','attacker_without_shadow'),
    ('controlled_tapped_blocker','untapped'),
})


@dataclass(frozen=True,slots=True)
class BlockAsThoughPermission:
    scope: str
    assumption: str
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.scope) is not str or type(self.assumption) is not str
                or (self.scope,self.assumption) not in _PAIRS):
            raise ValueError('Block as-though permission requires a closed version-one scope and assumption')

    def to_dict(self):
        return {'schema_version':self.schema_version,'scope':self.scope,'assumption':self.assumption}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','scope','assumption'}:
            raise ValueError('Block as-though permission fields changed')
        return cls(**dict(value))


@dataclass(frozen=True,slots=True)
class BlockAsThoughAssumptions:
    attacker_without_shadow: bool = False
    blocker_with_shadow: bool = False
    reach_against_dragons: bool = False
    untapped: bool = False

    def __post_init__(self):
        if any(type(getattr(self,field)) is not bool for field in self.__dataclass_fields__):
            raise ValueError('Block as-though assumptions require exact booleans')

    def keywords(self,attacker,blocker):
        for terms in (attacker,blocker):
            if type(terms) is not frozenset or any(type(s) is not str or not s or s!=s.casefold() or s!=s.strip() for s in terms):
                raise ValueError('Block as-though requires canonical keyword snapshots')
        # The second conditional reads the stated block evaluation, not a real
        # addition/removal of either participant's abilities (CR 609.4a).
        attacking=attacker-{'shadow'} if self.attacker_without_shadow else attacker
        blocking=blocker|({'shadow'} if self.blocker_with_shadow and 'shadow' in attacking else set())
        if self.reach_against_dragons:blocking=blocking|{'reach'}
        return frozenset(attacking),frozenset(blocking)


def blocking_permission_from_dict(value):
    """Decode only explicit source-owned blocking rule fragment kinds."""
    if value['kind']=='bounded_block_as_though':return BlockAsThoughPermission.from_dict(value['value'])
    if value['kind']=='landwalk_block_permission':return LandwalkBlockPermission.from_dict(value['value'])
    raise ValueError('Unknown source-owned blocking permission fragment kind')
